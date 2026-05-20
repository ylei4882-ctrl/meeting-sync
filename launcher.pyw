import os, sys, io

# Redirect stdout/stderr to log file to prevent pythonw crashes
log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.log")
sys.stdout = io.TextIOWrapper(open(log_path, "a", encoding="utf-8").buffer, encoding="utf-8")
sys.stderr = sys.stdout

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import meeting_app
meeting_app.MeetingApp().run()
