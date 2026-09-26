# One item per DJ, keyed by their WorkOS user id. Holds the address picked via Google Places.
resource "aws_dynamodb_table" "karaoke_dj" {
  name         = "karaoke-dj"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "djId"

  attribute {
    name = "djId"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }
}

# Singer profile + which DJ they are currently singing with.
resource "aws_dynamodb_table" "karaoke_singers" {
  name         = "karaoke-singers"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "singerId"

  attribute {
    name = "singerId"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }
}

# One item per singer per night (singerId + date). Each item carries a requestId,
# the djId for that night, and the list of songs requested. Kept forever so singers
# can browse their full history; the GSI serves the DJ's queue for a given night.
resource "aws_dynamodb_table" "karaoke_requested_songs" {
  name         = "karaoke-requested-songs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "singerId"
  range_key    = "date"

  attribute {
    name = "singerId"
    type = "S"
  }

  attribute {
    name = "date"
    type = "S"
  }

  attribute {
    name = "djId"
    type = "S"
  }

  global_secondary_index {
    name            = "djId-date-index"
    projection_type = "ALL"

    key_schema {
      attribute_name = "djId"
      key_type       = "HASH"
    }

    key_schema {
      attribute_name = "date"
      key_type       = "RANGE"
    }
  }

  point_in_time_recovery {
    enabled = true
  }
}
