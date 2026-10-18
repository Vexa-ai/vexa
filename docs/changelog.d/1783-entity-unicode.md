- **Knowledge-graph pages for names in any script (#1783).** Recording a person, company or
  connection whose name is written in Cyrillic, Greek, Chinese, Japanese, Arabic or another
  non-Latin script was refused ("an entity needs a name that survives slugification"). Cyrillic and
  Greek names are now transliterated into the page's file name (`Дмитрий Гранкин` →
  `dmitrii-grankin`); a name in a script without a transliteration gets a short stable id
  (`u-68dea76f`). The page title keeps the name as written, and every page that already existed
  keeps its path. A fact recorded without a source is now refused with an example of what a source
  looks like.
