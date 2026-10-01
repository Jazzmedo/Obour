{
  description = "Obour: run apps from remote Linux machines as local windows";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
      version = builtins.head (builtins.match ".*__version__ = \"([^\"]+)\".*"
        (builtins.readFile ./obour/__init__.py));
    in
    {
      packages = forAllSystems (pkgs:
        let
          python = pkgs.python3.withPackages (ps: [ ps.pygobject3 ]);
          # Tools Obour starts. Added after the user's PATH, so their own versions win.
          tools = with pkgs; [ openssh waypipe libnotify sshfs dconf (pkgs.xsetroot or pkgs.xorg.xsetroot) ];
          obour = pkgs.stdenvNoCC.mkDerivation {
            pname = "obour";
            inherit version;
            src = self;

            nativeBuildInputs = with pkgs; [ wrapGAppsHook4 gobject-introspection makeWrapper ];
            buildInputs = with pkgs; [ gtk4 libadwaita glib ];
            dontBuild = true;
            dontWrapGApps = true;

            installPhase = ''
              runHook preInstall
              mkdir -p $out/share/obour $out/bin
              cp -r obour bin $out/share/obour/
              install -Dm644 data/io.github.Jazzmedo.Obour.desktop -t $out/share/applications
              install -Dm644 data/io.github.Jazzmedo.Obour.svg -t $out/share/icons/hicolor/scalable/apps
              install -Dm644 data/io.github.Jazzmedo.Obour.metainfo.xml -t $out/share/metainfo
              install -Dm644 data/obour.1 -t $out/share/man/man1
              runHook postInstall
            '';

            # gappsWrapperArgs (typelibs, schemas, icons) is filled in before this runs.
            # OBOUR_BIN=obour: app-menu entries survive updates (store paths change).
            postFixup = ''
              makeWrapper ${python}/bin/python3 $out/bin/obour \
                --add-flags $out/share/obour/bin/obour \
                --set OBOUR_BIN obour \
                --suffix PATH : ${pkgs.lib.makeBinPath tools} \
                "''${gappsWrapperArgs[@]}"
            '';

            meta = with pkgs.lib; {
              description = "Run apps from remote Linux machines as local windows";
              homepage = "https://github.com/Jazzmedo/Obour";
              license = licenses.mit;
              platforms = platforms.linux;
              mainProgram = "obour";
            };
          };
        in
        {
          inherit obour;
          default = obour;
        });

      apps = forAllSystems (pkgs: {
        default = {
          type = "app";
          program = "${self.packages.${pkgs.stdenv.hostPlatform.system}.default}/bin/obour";
        };
      });

      # NixOS: imports = [ obour.nixosModules.default ];
      nixosModules.default = { pkgs, ... }: {
        environment.systemPackages = [ self.packages.${pkgs.stdenv.hostPlatform.system}.default ];
      };
    };
}
