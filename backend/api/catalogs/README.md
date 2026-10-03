# Song catalogs

The song lists that `lib/catalog.py` searches before falling back to YouTube.

| File | Source | Searched | Where it comes from |
| --- | --- | --- | --- |
| `karafun.csv` | KaraFun | first | The CSV download on the [KaraFun song list page](https://www.karafun.com/karaoke-song-list.html). Download it in a browser: the site turns away scripted downloads. |
| `stingray.csv` | Stingray Karaoke | second | Only used while the deployment has no Stingray Karaoke API credentials (`stingray_api` in Terraform). Stingray publishes no song list, so this one is yours to compile. |

Each file is a CSV with a header row containing `Title` and `Artist` columns, and optionally `Id`. Columns can be separated by commas or semicolons, and extra columns are ignored, so KaraFun's own song list export works without changes:

```csv
Id;Title;Artist;Year;Duo;Explicit;"Date Added";Styles;Languages
12543;Creep;Radiohead;1992;0;1;2008-03-07;Rock,Alternative;English
```

A source whose file is missing has no songs and is skipped. Terraform zips this directory into the search Lambdas, so a new or updated file takes effect on the next deploy.
