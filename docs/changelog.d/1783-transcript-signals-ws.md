- **Transcript signals reach the meeting view live (#1783).** A `/ws` subscription can now ask for
  `streams: ["signals"]` and receives `meeting.signals` frames: the signals service's score for each
  transcript line against the meeting's listening brief, and how much each word carries. They are
  read for the meeting the transcript access check granted, so exactly the people who may read a
  meeting's transcript see its signals; removing someone stops them as it stops the transcript. A
  subscribe that names no streams is unchanged.
