- **Teams: a bot name Teams refuses now fails in under half a minute and says why (#1780).** Teams accepts
  only letters, numbers, spaces and `- ' . _ @` in a guest's name. With any other character, such as
  parentheses, it keeps **Join now** disabled. The bot used to report that as waiting in the lobby
  until the admission timeout ran out. It now stops at the pre-join screen with `join_failure`, and its
  `last_error` starts with `teams_prejoin_blocked`, quotes Teams' rule and lists the characters it
  refused. Rename the bot to fix it. See [Bot participant name](/configuration#bot-participant-name).
