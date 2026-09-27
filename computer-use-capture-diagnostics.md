# Windows Computer Use capture failure

## Environment

- Windows 10 Home 22H2, build 19045.6466 (read from Windows version registry).
- Bundled computer-use plugin version 26.924.22138.
- Supported entry point: node_repl importing @oai/sky.
- Targets: File Explorer's mods window and The Sims 2 Mansion and Garden Stuff (Sims2EP9RPC.exe), windowed.

## Reproduction

Use sky.list_windows(), select the unique returned Explorer mods window, and rehydrate it with sky.get_window({id, app}). On the same returned window:

1. get_window_state({window, include_screenshot:false, include_text:true}) succeeds: 459 ms, accessibility tree present, 19,064 characters.
2. get_window_state({window, include_screenshot:true, include_text:false}) fails: 2,051 ms, Error: FrameArrived timed out: timed out waiting on channel.

Earlier attempts also returned: window capture timed out: timed out waiting on channel.

## Controls already tested

- Window discovery and game title-bar accessibility work.
- Both Explorer and the game fail screenshot capture.
- Activating Explorer before capture did not resolve it.
- Closing the game's phone dialog did not resolve it.
- Resetting the JavaScript kernel and reimporting @oai/sky did not resolve it.
- User restarted Codex; Explorer capture still failed afterward.
- User successfully captured Explorer with Snipping Tool and provided the PNG; the image was readable.
- TS2Bridge API pause detection is independently working and is not changed by this investigation.

## Local diagnostics

Codex desktop log at 2026-09-27T00:17:01.717Z reports computer-use native pipe startup ready. The bundled plugin was registered and its installed version reused at startup. Capture-specific searches of the inspected desktop logs and local log database did not expose a deeper cause beyond the tool errors. The log database hit was a recorded diagnostic command, not an independent native capture error.

No dedicated capture diagnostic method is listed in the bundled public sky API documentation or exposed tools. No undocumented helper calls were attempted. CIM queries for OS and GPU details were denied in the sandbox; the OS version was instead obtained from the registry. GPU details remain unknown.

## Conclusion

The reproducible failing operation is screenshot capture through the plugin. Discovery, accessibility, and the user-run Snipping Tool capture succeed. The underlying cause is unresolved. These observations do not prove a Windows-version incompatibility, a graphics driver fault, or a permissions problem. Snipping Tool success does not prove that the plugin uses an equivalent capture path.

No game/plugin/system settings were changed. No report was submitted externally.

## Successful follow-up after Windows 11 upgrade

At 2026-09-27T01:55:02Z, the end-to-end phone-menu test succeeded. The user reported upgrading to Windows 11. The API initially returned fresh=true, gamePaused=true, and screenCheckRecommended=true. Windows Computer Use captured the Personal Phonebook successfully. The agent visually identified and clicked its X cancel button. A follow-up screenshot showed the menu closed and the game clock advancing. A fresh API sample (1.1 seconds old) returned gamePaused=false, screenCheckRecommended=false, and screenCheckReason=null. No bridge changes were required. Capture works in this session after the upgrade; the precise cause of the earlier failure remains unproven. This was one supervised end-to-end test, not a persistent gameplay loop.
