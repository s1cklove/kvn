{ lib
, stdenvNoCC
, python3
, kvn
, wrapGAppsHook3
, gobject-introspection
, gtk3
, webkitgtk_4_1
, libayatana-appindicator
}:

let
  py = python3.withPackages (ps: [
    ps.pywebview
    ps.pygobject3
    ps.pystray
    ps.pillow
  ]);
in
stdenvNoCC.mkDerivation {
  pname = "kvn-ui";
  version = kvn.version;
  src = lib.cleanSource ../.;

  nativeBuildInputs = [ wrapGAppsHook3 gobject-introspection ];
  buildInputs = [ gtk3 webkitgtk_4_1 libayatana-appindicator ];

  dontBuild = true;
  dontWrapGApps = true;

  installPhase = ''
    runHook preInstall
    mkdir -p $out/share/kvn-ui $out/bin
    cp -r ui/app.py ui/web $out/share/kvn-ui/
    if [ -f ui/icon.png ]; then
      cp ui/icon.png $out/share/kvn-ui/icon.png
    elif [ -f icon.png ]; then
      cp icon.png $out/share/kvn-ui/icon.png
    fi

    cat > $out/bin/kvn-ui <<EOF
    #!${stdenvNoCC.shell}
    export PYTHONPATH="${kvn}/${python3.sitePackages}\''${PYTHONPATH:+:\$PYTHONPATH}"
    export PATH="${kvn}/bin:\$PATH"
    exec ${py}/bin/python3 $out/share/kvn-ui/app.py "\$@"
    EOF
    chmod +x $out/bin/kvn-ui
    runHook postInstall
  '';

  preFixup = ''
    wrapGApp $out/bin/kvn-ui
  '';

  meta = {
    description = "Isolated native desktop UI for KVN Connect";
    license = lib.licenses.mit;
    platforms = lib.platforms.linux;
    mainProgram = "kvn-ui";
  };
}
