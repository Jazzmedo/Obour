"""Ask for an SSH password, offering to set up key login so it isn't needed again."""

from __future__ import annotations

from gi.repository import Adw

from .. import auth
from ..i18n import _


def ask_login(parent, host: str, on_login, on_cancel=None, error: str = "") -> None:
    """on_login(password, set_up_key) runs when the user confirms."""
    body = _("{host} needs your SSH password.").format(host=host)
    if error:
        body = f"{error}\n\n{body}"
    dialog = Adw.AlertDialog(heading=_("Log In"), body=body)

    group = Adw.PreferencesGroup()
    password_row = Adw.PasswordEntryRow(title=_("Password"), activates_default=True)
    has_key = auth.local_public_key() is not None
    key_row = Adw.SwitchRow(
        title=_("Don't ask again"),
        subtitle=(_("Copy your SSH key to this host so no password is needed next time")
                  if has_key else
                  _("Create an SSH key and copy it to this host so no password is needed "
                    "next time")),
        active=True,
    )
    group.add(password_row)
    group.add(key_row)
    dialog.set_extra_child(group)

    dialog.add_response("cancel", _("Cancel"))
    dialog.add_response("login", _("Log In"))
    dialog.set_response_appearance("login", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("login")
    dialog.set_close_response("cancel")
    dialog.set_response_enabled("login", False)
    password_row.connect("changed",
                         lambda row: dialog.set_response_enabled("login", bool(row.get_text())))

    def on_response(_dialog, response):
        if response == "login":
            on_login(password_row.get_text(), key_row.get_active())
        elif on_cancel:
            on_cancel()

    dialog.connect("response", on_response)
    dialog.present(parent)
    password_row.grab_focus()
