import os

# Most tests call the API without signing in. Sign-in has its own tests
# (test_auth.py), which turn it on.
os.environ.setdefault("SFS_AUTH", "none")
