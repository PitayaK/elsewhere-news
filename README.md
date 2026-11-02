# elsewhere-news

Read Elsewhere reporting, podcasts and structured facts on China's technology,
AI, startup and venture ecosystem. Connect an Elsewhere reader account for
read-only personal context.

Send this to your Agent:

> 请安装并连接 elsewhere-news：https://elsewhere.news/skill.md

For ChatGPT or Dots, use the host's native plugin connection. If your Agent
cannot install it automatically, follow the public guide:
[中文](https://elsewhere.news/zh/docs#connect-chatgpt) ·
[English](https://elsewhere.news/en/docs#connect-chatgpt).
Complete Elsewhere sign-in and consent in the host's browser flow, then let
the Agent verify the connection. Later conversations and daily tasks reuse it.

Use the [versioned GitHub release](https://github.com/PitayaK/elsewhere-news/releases/tag/v3.0.2)
and its `elsewhere-news-3.0.2.zip` asset
when your host supports package import. GitHub's source-code ZIP is not that
installation package. The built ZIP is importable, not a universal one-click
install link; host account and workspace policies still apply. No API key or
token needs to be copied into the conversation.

The same package is also available from
[elsewhere.news](https://elsewhere.news/.well-known/elsewhere-skill/elsewhere-news-3.0.2.zip).

## Source and package

The public source is [PitayaK/elsewhere-news](https://github.com/PitayaK/elsewhere-news).
`SKILL.md` and `agents/openai.yaml` are the canonical Skill sources.
`plugin.json` and `mcp.json` declare the portable plugin and Remote MCP.

Build the four-file ZIP with Python's standard library:

```sh
python3 scripts/build_plugin.py --output /absolute/path/elsewhere-news-3.0.2.zip
```

The ZIP includes only the two manifests, the Skill, and its dependency metadata.
The build script and this README are not shipped in the plugin.
