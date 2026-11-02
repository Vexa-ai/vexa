- **Terminal: a Teams link with a malformed `%`-escape no longer breaks "Send bot" (#2011).** Pasting
  a Teams meeting link that has been cut off mid-query (for example `…?context=%7b%22Tid%22%3a%2`)
  or that contains a stray `%` previously did nothing at all. The link parser threw, and neither a
  bot nor an error appeared. The Terminal now decodes such links as leniently as the server does,
  takes the meeting's thread id from them, and sends the bot.
