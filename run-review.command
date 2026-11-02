#!/bin/zsh
cd -- "$(dirname -- "$0")" || exit 1
print "Starting Layout/review…"
.venv/bin/python app.py --open
status=$?
if (( status != 0 )); then
  print ""
  print "The server did not start. Copy the error above and send it back."
  print "Press Return to close this window."
  read
fi
exit $status
