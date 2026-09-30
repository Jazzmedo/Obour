# Contributing to Obour

Thanks for helping! Bug reports, translations and small fixes are all welcome.

## Report a bug or ask for a feature

Open an [issue](https://github.com/Jazzmedo/Obour/issues/new/choose) and pick a template.
For bugs, the app's log helps most: in Obour, main menu → *Show Log*, then copy the lines
around the problem.

## Run it from source

```sh
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 openssh-client waypipe
git clone https://github.com/Jazzmedo/Obour.git && cd Obour
bin/obour
```

(Fedora: `python3-gobject gtk4 libadwaita`; Arch: `python-gobject gtk4 libadwaita`.)

## Make a change

1. Fork the repo and create a branch.
2. Keep the change small and focused; match the style of the code around it.
3. Run the tests:

   ```sh
   python3 -m unittest discover tests
   ```

4. Try it with a real app on a real remote computer if your change touches launching,
   sound, the clipboard or drag and drop.
5. Open a pull request saying what it changes and how you tested it.

`docs/HOW-IT-WORKS.md` explains how the pieces fit together.

## Translate

Interface text lives in `obour/locale/<language>.po`. After changing strings in the code,
run `packaging/i18n/update-po.sh`, then fill in the empty `msgstr` lines (a text editor,
Poedit or GNOME's Translation Editor all work). To add a language, copy `obour.pot` to
`<language>.po` and add it to `LANGUAGES` in `obour/i18n.py`.

## License

By contributing, you agree that your work is released under the [MIT license](LICENSE).
