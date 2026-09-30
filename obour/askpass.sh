#!/bin/sh
# SSH_ASKPASS helper: answer ssh's password prompt with the password Obour passed in
# this ssh process's environment. Anything else (key passphrases, questions) is declined.
case "$1" in
    *assword*) printf '%s\n' "$OBOUR_SSH_PASSWORD" ;;
    *) exit 1 ;;
esac
