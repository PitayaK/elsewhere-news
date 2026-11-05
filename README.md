# elsewhere-news

Read Elsewhere reporting, podcasts and account-linked knowledge graphs about
China's technology, AI, startup and venture ecosystem.

Send this to your Agent:

> 请安装并连接 elsewhere-news：https://elsewhere.news/skill.md

The Agent installs the Skill and runs its included connection client. The
client opens Elsewhere sign-in in the browser, completes authorization, and
reuses it for later requests. Knowledge-graph and personal reads remain tied
to the connected Elsewhere account. The Agent does not need to register a
server in the Harness's MCP settings.

## Source and package

The Skill, client, metadata and package builder are maintained together in
[PitayaK/elsewhere-news](https://github.com/PitayaK/elsewhere-news).
`SKILL.md` is the entrypoint; `scripts/elsewhere.py` is the included Python
3.10+ connection client. `agents/openai.yaml` supplies display metadata without
requesting host MCP installation.

Build a reviewed package with Python's standard library:

```sh
python3 scripts/build_plugin.py --output /absolute/path/elsewhere-news-3.1.0.zip
```

The builder requires the client pin in `SKILL.md` to match its actual bytes.
It refuses unresolved development pins. The portable ZIP contains only
`plugin.json`, the Skill, its display metadata and its reviewed client. It
contains no credentials, personal context, build/test scripts or automatic
MCP registration. GitHub's generated source-code ZIP is not that installation
package.

The previous declarative MCP configuration is preserved in
`integrations/native-mcp/mcp.json` for explicitly chosen native integrations;
it is not bundled or automatically discovered from the repository root.

## Development acceptance

Version 3.1.0 / bundle 3 adds executable connection code to the earlier
instruction-only bundle. Development and staging must verify a fresh browser
login, authenticated graph reads, safe credential persistence, refresh and
revocation before production release. Runtime execution, an interactive
browser flow and a safe persistent credential store are required; a successful
file download alone does not prove this flow works in a given runtime.
