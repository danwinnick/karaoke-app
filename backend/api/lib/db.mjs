import crypto from 'node:crypto';
import { DynamoDBClient } from '@aws-sdk/client-dynamodb';
import {
  BatchGetCommand,
  DynamoDBDocumentClient,
  GetCommand,
  PutCommand,
  QueryCommand,
  ScanCommand,
  UpdateCommand,
} from '@aws-sdk/lib-dynamodb';

const doc = DynamoDBDocumentClient.from(new DynamoDBClient({}), {
  marshallOptions: { removeUndefinedValues: true },
});

const DJ_TABLE = process.env.DJ_TABLE;
const SINGERS_TABLE = process.env.SINGERS_TABLE;
const SONGS_TABLE = process.env.SONGS_TABLE;
const SONGS_DJ_INDEX = process.env.SONGS_DJ_INDEX;

const nowIso = () => new Date().toISOString();

// ---- DJs -------------------------------------------------------------------

export async function getDj(djId) {
  const { Item } = await doc.send(new GetCommand({ TableName: DJ_TABLE, Key: { djId } }));
  return Item ?? null;
}

export async function putDj(dj) {
  const existing = await getDj(dj.djId);
  const item = { ...dj, createdAt: existing?.createdAt ?? nowIso(), updatedAt: nowIso() };
  await doc.send(new PutCommand({ TableName: DJ_TABLE, Item: item }));
  return item;
}

export async function listDjs() {
  const djs = [];
  let ExclusiveStartKey;
  do {
    const page = await doc.send(
      new ScanCommand({
        TableName: DJ_TABLE,
        ProjectionExpression: 'djId, #n, address, lat, lng',
        ExpressionAttributeNames: { '#n': 'name' },
        ExclusiveStartKey,
      }),
    );
    djs.push(...page.Items);
    ExclusiveStartKey = page.LastEvaluatedKey;
  } while (ExclusiveStartKey);
  return djs;
}

export async function getDjNames(djIds) {
  const ids = [...new Set(djIds)].filter(Boolean);
  const names = {};
  for (let i = 0; i < ids.length; i += 100) {
    const { Responses } = await doc.send(
      new BatchGetCommand({
        RequestItems: {
          [DJ_TABLE]: {
            Keys: ids.slice(i, i + 100).map((djId) => ({ djId })),
            ProjectionExpression: 'djId, #n',
            ExpressionAttributeNames: { '#n': 'name' },
          },
        },
      }),
    );
    for (const dj of Responses[DJ_TABLE] ?? []) names[dj.djId] = dj.name;
  }
  return names;
}

// ---- Singers ---------------------------------------------------------------

export async function getSinger(singerId) {
  const { Item } = await doc.send(new GetCommand({ TableName: SINGERS_TABLE, Key: { singerId } }));
  return Item ?? null;
}

export async function upsertSinger({ singerId, name, email }) {
  await doc.send(
    new UpdateCommand({
      TableName: SINGERS_TABLE,
      Key: { singerId },
      UpdateExpression: 'SET #n = :name, email = :email, updatedAt = :now, createdAt = if_not_exists(createdAt, :now)',
      ExpressionAttributeNames: { '#n': 'name' },
      ExpressionAttributeValues: { ':name': name, ':email': email, ':now': nowIso() },
    }),
  );
}

export async function setSingerDj(singerId, djId) {
  await doc.send(
    new UpdateCommand({
      TableName: SINGERS_TABLE,
      Key: { singerId },
      UpdateExpression: 'SET currentDjId = :djId, updatedAt = :now',
      ExpressionAttributeValues: { ':djId': djId, ':now': nowIso() },
    }),
  );
}

// ---- Requested songs (one item per singer per night) ------------------------

export async function getNight(singerId, date) {
  const { Item } = await doc.send(new GetCommand({ TableName: SONGS_TABLE, Key: { singerId, date } }));
  return Item ?? null;
}

export async function queryDjNight(djId, date) {
  const items = [];
  let ExclusiveStartKey;
  do {
    const page = await doc.send(
      new QueryCommand({
        TableName: SONGS_TABLE,
        IndexName: SONGS_DJ_INDEX,
        KeyConditionExpression: 'djId = :djId AND #d = :date',
        ExpressionAttributeNames: { '#d': 'date' },
        ExpressionAttributeValues: { ':djId': djId, ':date': date },
        ExclusiveStartKey,
      }),
    );
    items.push(...page.Items);
    ExclusiveStartKey = page.LastEvaluatedKey;
  } while (ExclusiveStartKey);
  return items;
}

export async function appendSong({ singerId, singerName, date, djId, song }) {
  const { Attributes } = await doc.send(
    new UpdateCommand({
      TableName: SONGS_TABLE,
      Key: { singerId, date },
      UpdateExpression:
        'SET songs = list_append(if_not_exists(songs, :empty), :song), djId = :djId, singerName = :name, ' +
        'requestId = if_not_exists(requestId, :rid), createdAt = if_not_exists(createdAt, :now), updatedAt = :now',
      ExpressionAttributeValues: {
        ':empty': [],
        ':song': [song],
        ':djId': djId,
        ':name': singerName,
        ':rid': crypto.randomUUID(),
        ':now': nowIso(),
      },
      ReturnValues: 'ALL_NEW',
    }),
  );
  return Attributes;
}

// Moves tonight's still-queued songs to a new DJ (at the back of their line).
export async function moveNightToDj(item, djId) {
  const now = Date.now();
  const songs = (item.songs ?? []).map((song, i) =>
    song.status === 'queued' ? { ...song, djId, order: now + i } : song,
  );
  await doc.send(
    new UpdateCommand({
      TableName: SONGS_TABLE,
      Key: { singerId: item.singerId, date: item.date },
      UpdateExpression: 'SET songs = :songs, djId = :djId, updatedAt = :now',
      ConditionExpression: 'updatedAt = :prev',
      ExpressionAttributeValues: { ':songs': songs, ':djId': djId, ':now': nowIso(), ':prev': item.updatedAt },
    }),
  );
}

export async function updateSong({ singerId, date, index, songId, set }) {
  const names = {};
  const values = { ':sid': songId, ':now': nowIso() };
  const sets = ['updatedAt = :now'];
  Object.entries(set).forEach(([key, value], i) => {
    names[`#f${i}`] = key;
    values[`:v${i}`] = value;
    sets.push(`songs[${index}].#f${i} = :v${i}`);
  });
  await doc.send(
    new UpdateCommand({
      TableName: SONGS_TABLE,
      Key: { singerId, date },
      UpdateExpression: `SET ${sets.join(', ')}`,
      ConditionExpression: `songs[${index}].songId = :sid`,
      ExpressionAttributeNames: names,
      ExpressionAttributeValues: values,
    }),
  );
}

// Records tonight's tip (once per night) and optionally moves a song to a new order.
export async function recordTip({ singerId, date, djId, boost }) {
  const values = { ':tip': { djId, at: nowIso() }, ':now': nowIso() };
  let update = 'SET tip = :tip, updatedAt = :now';
  let condition = 'attribute_exists(singerId) AND attribute_not_exists(tip)';
  if (boost) {
    update += `, songs[${boost.index}].#o = :order`;
    condition += ` AND songs[${boost.index}].songId = :sid`;
    values[':order'] = boost.order;
    values[':sid'] = boost.songId;
  }
  await doc.send(
    new UpdateCommand({
      TableName: SONGS_TABLE,
      Key: { singerId, date },
      UpdateExpression: update,
      ConditionExpression: condition,
      ...(boost ? { ExpressionAttributeNames: { '#o': 'order' } } : {}),
      ExpressionAttributeValues: values,
    }),
  );
}

export async function historyPage(singerId, cursor, limit = 20) {
  let startKey;
  if (cursor) {
    try {
      startKey = JSON.parse(Buffer.from(cursor, 'base64url').toString('utf8'));
    } catch {
      startKey = null;
    }
    if (startKey?.singerId !== singerId) throw new Error('Invalid history cursor');
  }
  const { Items, LastEvaluatedKey } = await doc.send(
    new QueryCommand({
      TableName: SONGS_TABLE,
      KeyConditionExpression: 'singerId = :s',
      ExpressionAttributeValues: { ':s': singerId },
      ScanIndexForward: false,
      Limit: limit,
      ExclusiveStartKey: startKey,
    }),
  );
  return {
    items: Items,
    cursor: LastEvaluatedKey ? Buffer.from(JSON.stringify(LastEvaluatedKey)).toString('base64url') : null,
  };
}
