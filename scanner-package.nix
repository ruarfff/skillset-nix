{
  lib,
  stdenv,
  fetchurl,
  python3,
}:
let
  # Upstream wheels include the platform-specific CEL helper. All runtime
  # dependencies are supplied by Nix; installation never invokes pip.
  wheels = {
    aarch64-darwin = {
      url = "https://files.pythonhosted.org/packages/5e/b5/a16cbb76ef9c7428409f2e74d48b42ad02329d44d9782978bb8c9125b207/cisco_ai_skill_scanner-2.1.0-cp311.cp312.cp313.cp314-none-macosx_13_0_arm64.whl";
      hash = "sha256-RSgRpkOZp/X2ZW9fzLVIel86DKAOjN+khHYQItqFK/Y=";
    };
    x86_64-darwin = {
      url = "https://files.pythonhosted.org/packages/95/c0/f0e1ad8bb6be5e6b0fe0e6a428e863f420c1789d2b56bd411d8dcec16e98/cisco_ai_skill_scanner-2.1.0-cp311.cp312.cp313.cp314-none-macosx_13_0_x86_64.whl";
      hash = "sha256-VeeelhvJbPeHu0G7W3nE3U9DSrvVQSXfK9lIi1wXlBI=";
    };
    aarch64-linux = {
      url = "https://files.pythonhosted.org/packages/f8/0b/9180f9b582cf3a5eb7270d6adf8d35c68d742610179b6e102e72507e7b72/cisco_ai_skill_scanner-2.1.0-cp311.cp312.cp313.cp314-none-manylinux_2_17_aarch64.whl";
      hash = "sha256-+GdVfZuqfGZUshyYNxckDvr4sLLwBK6IgeiJV8z2nPw=";
    };
    x86_64-linux = {
      url = "https://files.pythonhosted.org/packages/ff/be/5b468d653ece56ab24497b6bb308e0487cc6b4b1b77bb1ebd4b5eb26a19c/cisco_ai_skill_scanner-2.1.0-cp311.cp312.cp313.cp314-none-manylinux_2_17_x86_64.whl";
      hash = "sha256-yEKStyC/Dt3IkT/jAX3NsFvX6Y6xn27mHe4sTrn6kB4=";
    };
  };
  python = python3.override {
    packageOverrides = final: previous: {
      # These upstream suites pull large dataset and provider SDK stacks that
      # local scans do not use (Arrow also fails on Intel macOS). Keep import
      # checks for the packaged dependencies.
      tokenizers = previous.tokenizers.overridePythonAttrs (_: {
        doCheck = false;
        nativeCheckInputs = [ ];
        postUnpack = "";
      });
      anthropic = previous.anthropic.overridePythonAttrs (_: {
        doCheck = false;
        nativeCheckInputs = [ ];
      });
      # The scanner uses LiteLLM's SDK. Its proxy web UI adds over 2 GB and
      # is not part of the scanner's CLI or API.
      litellm = previous.litellm.overridePythonAttrs (old: {
        postInstall = (old.postInstall or "") + ''
          rm -rf "$out/${python.sitePackages}/litellm/proxy/_experimental/out" \
            "$out/${python.sitePackages}/litellm/proxy/swagger"
        '';
      });
      # Keep upstream's security floors and protobuf compatibility bound.
      click = final.buildPythonPackage {
        pname = "click";
        version = "8.3.3";
        format = "wheel";
        src = fetchurl {
          url = "https://files.pythonhosted.org/packages/ae/44/c1221527f6a71a01ec6fbad7fa78f1d50dfa02217385cf0fa3eec7087d59/click-8.3.3-py3-none-any.whl";
          hash = "sha256-or9Cm7MDPIn6STb/s11ctHHjcZ4fPIp8P/8LgxQwVhM=";
        };
        pythonImportsCheck = [ "click" ];
        meta.license = lib.licenses.bsd3;
      };
      python-multipart = final.buildPythonPackage {
        pname = "python-multipart";
        version = "0.0.32";
        format = "wheel";
        src = fetchurl {
          url = "https://files.pythonhosted.org/packages/e1/04/e8135ebd1ad02c56ec633277529b2602ff99ff634be76cdba5744cf554fd/python_multipart-0.0.32-py3-none-any.whl";
          hash = "sha256-/20/d28Wh4yJTlLhBylv/IkOkTxhGxpOxsROKCH+LiM=";
        };
        pythonImportsCheck = [ "python_multipart" ];
        meta.license = lib.licenses.asl20;
      };
      protobuf = final.buildPythonPackage {
        pname = "protobuf";
        version = "6.33.6";
        format = "wheel";
        src = fetchurl {
          url = "https://files.pythonhosted.org/packages/c4/72/02445137af02769918a93807b2b7890047c32bfb9f90371cbc12688819eb/protobuf-6.33.6-py3-none-any.whl";
          hash = "sha256-dxeeAGxHbmm/jozoZmQAkexC4b64CyE8OQAAbs+6aQE=";
        };
        pythonImportsCheck = [ "google.protobuf" ];
        meta.license = lib.licenses.bsd3;
      };
      pdfid = final.buildPythonPackage {
        pname = "pdfid";
        version = "1.1.3";
        format = "wheel";
        src = fetchurl {
          url = "https://files.pythonhosted.org/packages/29/48/9ba402d773ffac76515720f98f3a01a2802737b6a2b75cac1fb8ba269a7a/pdfid-1.1.3-py3-none-any.whl";
          hash = "sha256-m5tyFFqBdZxunzJ+sOEVowo/vRQPpb1doNGVbsTJ9lo=";
        };
        pythonImportsCheck = [ "pdfid.pdfid" ];
        meta.license = lib.licenses.publicDomain;
      };
    };
  };
in
python.pkgs.buildPythonApplication {
  pname = "cisco-ai-skill-scanner";
  version = "2.1.0";
  format = "wheel";
  src = fetchurl wheels.${stdenv.hostPlatform.system};
  # The scanner verifies the bundled helper against its upstream manifest.
  dontStrip = true;
  dependencies =
    with python.pkgs;
    [
      pyyaml
      python-frontmatter
      rich
      textual
      tabulate
      pydantic
      protobuf
      fastapi
      uvicorn
      python-multipart
      yara-x
      python-dotenv
      httpx
      magika
      pdfid
      oletools
      confusable-homoglyphs
      anthropic
      openai
      litellm
      click
    ]
    ++ python.pkgs.uvicorn.optional-dependencies.standard;
  pythonImportsCheck = [ "skill_scanner" ];
  # LiteLLM otherwise attempts to refresh its pricing table during import.
  env.LITELLM_LOCAL_MODEL_COST_MAP = "True";
  makeWrapperArgs = [ "--set LITELLM_LOCAL_MODEL_COST_MAP True" ];
  meta = {
    description = "Security scanner for agent skills with local static analysis";
    homepage = "https://github.com/cisco-ai-defense/skill-scanner";
    license = lib.licenses.asl20;
    platforms = builtins.attrNames wheels;
    mainProgram = "skill-scanner";
  };
}
