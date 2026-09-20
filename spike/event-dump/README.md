# event-dump spike

A minimal plugin that writes every `HERDR_*` variable (including the context and event JSON) to its
state directory. It was used to capture the real payloads kept in `tests/fixtures/herdr_events/`.

The manifest is stored as `herdr-plugin.toml.example` so the marketplace does not list it as a plugin.
To use it again:

```bash
cp herdr-plugin.toml.example herdr-plugin.toml
herdr plugin link "$PWD"
# ... trigger events, then:
herdr plugin unlink maaalo.herdr-markmap-spike
rm herdr-plugin.toml
```
