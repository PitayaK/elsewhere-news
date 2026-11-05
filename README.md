# elsewhere-news

Read Elsewhere reporting, podcasts and account-linked knowledge graphs about
China's technology, AI, startup and venture ecosystem.

Send this to your Agent:

> 请安装并连接 elsewhere-news：https://elsewhere.news/skill.md

The Agent installs the Skill and runs its included connection client in its
own persistent environment. It opens Elsewhere sign-in in the browser and
reuses authorization for later requests and scheduled work. You sign into your
Elsewhere reader account and approve access; no MCP settings or key handling
are needed. Knowledge-graph and personal reads stay tied to that account.

For a cloud Agent, the client and saved state stay on its cloud computer, so
your own computer can be off. The host must provide persistent private storage,
HTTPS execution and a browser sign-in path. A disposable runtime cannot retain
the connection. Each scheduled run reuses the existing connection and verifies
the expected Elsewhere account before reading data.

The client uses macOS Keychain or a Linux private directory (`0700`) and
credential file (`0600`) outside workspaces, Skill caches and synchronized or
exported folders. Linux permissions restrict access by other OS identities;
the file is not promised to be encrypted or hidden from processes running as
the same user. The normal client interface never returns tokens to the Agent.
Authorization uses a fixed HTTPS callback relay, allowing the browser
and command execution to use different networks.

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
login, authenticated graph reads, credential persistence, refresh and
revocation before production release. Acceptance also needs a later fresh
process and a scheduled cloud run with the user's computer off. File permissions
and a successful download do not establish that the host retains the private
state across those runs. Host-required approvals and login verification still
apply.
