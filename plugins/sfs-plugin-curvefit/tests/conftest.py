import os

# The tests call the app's API without signing in (sign-in tests live in backend/tests)
os.environ.setdefault("SFS_AUTH", "none")
