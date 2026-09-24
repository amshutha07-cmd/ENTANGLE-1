#!/usr/bin/env bash
# push_to_github.sh — upload this whole project to a PRIVATE GitHub repository as one clean commit.
#
#   bash push_to_github.sh
#
# It refuses to continue if a secret or build artifact would be uploaded, shows you what will go,
# and asks you to confirm the repository is private. It never forces anything.
set -euo pipefail
cd "$(dirname "$0")"

REPO_URL="https://github.com/adarshshkla/ANSxVault1.git"
BRANCH="clean-main"      # local branch with a single fresh commit (avoids uploading the old 4,500 build files)
TARGET="main"            # branch name on GitHub

echo "== 1/5  Preparing a clean commit on branch '$BRANCH'"
if git rev-parse --verify --quiet "$BRANCH" >/dev/null; then
  git checkout "$BRANCH"
else
  git checkout --orphan "$BRANCH"
fi
git add -A

echo "== 2/5  Safety checks"
bad="$(git ls-files | grep -i -E 'storage_targets|(^|/)\.env$|relay_data/|\.dmg$|\.pem$|\.ansx_id$|(^|/)identities/|(^|/)\.ansx_vault/|libshatter\.(so|dylib)$|shatter\.dll$' || true)"
if [ -n "$bad" ]; then
  echo "STOP. These files must not be uploaded:"
  echo "$bad"
  echo "Nothing was pushed. Remove them from the folder or add them to .gitignore, then run this again."
  exit 1
fi
big=""
while IFS= read -r f; do
  if [ -f "$f" ]; then
    size=$(wc -c < "$f")
    if [ "$size" -gt 50000000 ]; then big="$big\n  $f ($((size / 1048576)) MB)"; fi
  fi
done < <(git ls-files)
if [ -n "$big" ]; then
  echo "STOP. Files over 50 MB (GitHub warns at 50 MB and rejects over 100 MB):"
  printf "%b\n" "$big"
  exit 1
fi
count=$(git ls-files | wc -l | tr -d ' ')
echo "OK: no secrets or build artifacts. $count files will be uploaded."

echo "== 3/5  What will be uploaded (top-level)"
git ls-files | awk -F/ '{print $1}' | sort -u | sed 's/^/   /'

echo
echo "== 4/5  Privacy check"
echo "A PUBLIC repository would publish your design (bad for a patent) and your documented security limits."
echo "Open https://github.com/adarshshkla/ANSxVault1  ->  Settings  ->  General  ->  Danger Zone."
read -r -p "Is the repository set to PRIVATE? Type the word private to continue: " answer
if [ "$(echo "$answer" | tr '[:upper:]' '[:lower:]')" != "private" ]; then
  echo "Cancelled. Nothing was pushed."
  exit 1
fi

echo "== 5/5  Committing and pushing"
git commit -m "A.N.Sx Vault: encrypted file transfer with relay, cloud storage and desktop app" || echo "(nothing new to commit)"
if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "$REPO_URL"
else
  git remote add origin "$REPO_URL"
fi
echo "GitHub will ask you to sign in. Use a Personal Access Token (not your password), or 'gh auth login' first."
git push -u origin "$BRANCH:$TARGET"

echo
echo "Done. Open https://github.com/adarshshkla/ANSxVault1 to check the files."
