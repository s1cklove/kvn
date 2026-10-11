{ lib, python3Packages, sing-box, libnotify, makeWrapper }:

python3Packages.buildPythonApplication {
  pname = "kvn";
  version = "0.1.0";
  pyproject = true;
  src = lib.cleanSource ../.;

  build-system = [ python3Packages.setuptools ];
  nativeBuildInputs = [ makeWrapper ];
  nativeCheckInputs = [ sing-box ];

  # Pin the runtime tools so the service works without them on PATH.
  postFixup = ''
    wrapProgram $out/bin/kvn \
      --set-default KVN_SING_BOX ${lib.getExe sing-box} \
      --set-default KVN_NOTIFY_SEND ${libnotify}/bin/notify-send
  '';

  checkPhase = ''
    runHook preCheck
    python -m unittest discover -s tests
    runHook postCheck
  '';

  meta = {
    description = "Keeps a sing-box proxy on a BlancVPN subscription alive";
    license = lib.licenses.mit;
    platforms = lib.platforms.linux;
    mainProgram = "kvn";
  };
}
