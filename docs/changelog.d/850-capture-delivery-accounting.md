### Added

**Mixed capture reports where the audio went.** The capture node now counts what the audio graph
rendered against what the ScriptProcessor delivered and what the silence gate refused, and logs the
split. A frame missing downstream was previously unattributable on this lane — the gmeet lane has
always had its seen/emitted counters, the mixed lane had none, which is how a 35% capture deficit
ran unnoticed on jitsi/zoom/teams bots.
