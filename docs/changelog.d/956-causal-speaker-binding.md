- **Jitsi speaker names now wait for causal evidence in offline replay (#956).** Same-name
  dominant-speaker heartbeats no longer impersonate fresh acoustic onsets: turns remain provisional
  until sealed, complete custody proves one globally unique ordinal turn for the transition and
  the signal advances past the acoustic end. If later evidence contradicts that custody, the same
  stable transcript segment is repainted back to provisional instead of preserving a fabricated name.
  This slice is replay-proven; cross-platform live validation remains on the issue-backed train.
