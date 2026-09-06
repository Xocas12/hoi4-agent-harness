# Fair play and legal notes

**Single-player only.** This harness plays the game the way a person does:
reading visible state and issuing the inputs a player could issue. That is fine
in single-player, and it is not fine against other people. Do not use it in
multiplayer, in ranked or competitive settings, or anywhere an automated player
would be facing humans who did not agree to it.

**No game content here.** This repository contains no Paradox Interactive files,
assets, save data, or decompiled code. Hearts of Iron IV is a Paradox product;
you need your own copy, and your use of it is governed by their EULA and terms,
not by this project's license.

**No memory editing, no injection.** The adapters read save files the game itself
wrote, capture the screen, and send keyboard and mouse input. Nothing here reads
or writes another process's memory, patches the executable, or hooks the engine.
If you extend this project, keep it that way — that boundary is the difference
between an automated player and a cheat tool.

**Console commands are debug tools.** HOI4's console is a developer feature that
disables achievements. If you wire it into an adapter, label it as a debug path
and keep it out of anything used to score an agent — a run that used console
commands is not comparable to one that did not.

**Be honest about results.** If you publish scores from this harness, publish the
adapter, the model, the wake rule, and the token spend alongside them. An agent
that scored well with a hand-tuned reflex layer doing most of the work is a
result about the reflex layer.
