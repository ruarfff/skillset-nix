{
  lib,
  stdenvNoCC,
  python3,
  makeWrapper,
}:
stdenvNoCC.mkDerivation {
  pname = "skillset";
  version = "0.1.0";
  src = lib.cleanSource ./.;
  nativeBuildInputs = [ makeWrapper ];
  nativeCheckInputs = [ python3 ];
  dontBuild = true;
  doCheck = true;
  checkPhase = ''
    runHook preCheck
    python3 -B -m unittest discover -s tests -v
    runHook postCheck
  '';
  installPhase = ''
    runHook preInstall
    mkdir -p "$out/lib/skillset" "$out/bin" "$out/share/doc/skillset"
    cp python/*.py "$out/lib/skillset/"
    cp LICENSE NOTICE.md "$out/share/doc/skillset/"
    makeWrapper ${python3}/bin/python3 "$out/bin/skillset" \
      --add-flags "-B $out/lib/skillset/skill_vendor.py"
    runHook postInstall
  '';
  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck
    "$out/bin/skillset" --help >/dev/null
    "$out/bin/skillset" --root ${./tests/fixtures/all-skills} --validate
    runHook postInstallCheck
  '';
  meta = {
    description = "Update and validate unmodified agent skill snapshots";
    license = lib.licenses.mit;
    platforms = lib.platforms.unix;
    mainProgram = "skillset";
  };
}
