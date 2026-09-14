{ pkgs, home-manager }:
let
  inherit (pkgs) lib;
  makeHome =
    skillset:
    home-manager.lib.homeManagerConfiguration {
      inherit pkgs;
      modules = [
        ../modules/home-manager.nix
        {
          home.username = "example";
          home.homeDirectory =
            if pkgs.stdenv.hostPlatform.isDarwin then "/Users/example" else "/home/example";
          home.stateVersion = "24.11";
          programs.skillset = skillset;
        }
      ];
    };
  hello = ../examples/skills/local/hello;
  valid = settings: (builtins.tryEval (makeHome settings).config.home.username).success;
  base = {
    enable = true;
    skills.hello.path = hello;
    targets.agents.skills = [ "hello" ];
  };
  withTarget = target: base // { targets.agents = target; };
  expectedPaths = {
    agents = ".agents/skills";
    codex = ".agents/skills";
    opencode = ".config/opencode/skills";
    claude = ".claude/skills";
    copilot = ".copilot/skills";
    cursor = ".cursor/skills";
    antigravity = ".gemini/config/skills";
    antigravity-cli = ".gemini/antigravity-cli/skills";
    gemini = ".gemini/skills";
    pi = ".pi/agent/skills";
  };
  presetHomes = lib.mapAttrs (
    name: _: makeHome (base // { targets.${name}.allSkills = true; })
  ) expectedPaths;
  presets = makeHome (
    base
    // {
      targets = lib.mapAttrs (_: _: { skills = [ "hello" ]; }) (
        builtins.removeAttrs expectedPaths [ "codex" ]
      );
    }
  );
  overridden = makeHome (
    base
    // {
      targets = {
        claude = {
          path = ".config/custom-claude/skills";
          allSkills = true;
        };
        work = {
          path = ".work-agent/skills";
          skills = [ "hello" ];
        };
        cursor = {
          enable = false;
          allSkills = true;
        };
      };
    }
  );
  selected = makeHome (
    base
    // {
      targets = {
        agents.skills = [ "hello" ];
        second = {
          path = "Library/Application Support/example/skills";
          skills = [ "hello" ];
        };
        disabled = {
          enable = false;
          path = "../invalid";
          skills = [ "missing" ];
        };
      };
    }
  );
  rooted = makeHome {
    enable = true;
    root = ../examples/skills;
    targets.agents.skills = [ "hello" ];
  };
  live = makeHome (
    base
    // {
      skills.hello = {
        path = hello;
        livePath = "/tmp/skillset-live-fixture/hello";
      };
    }
  );
  generated = makeHome (
    base
    // {
      skills.hello.path = pkgs.runCommand "generated-hello-skill" { } ''
        cp -R ${hello} "$out"
      '';
    }
  );
  stringPath = makeHome (base // { skills.hello.path = toString hello; });
  allSettings = {
    enable = true;
    root = ./fixtures/all-skills;
    skills.extra = {
      path = pkgs.writeTextDir "SKILL.md" ''
        ---
        name: extra
        description: Additional skill for module tests.
        ---
        Use helper to print a greeting.
      '';
      requires = [ "helper" ];
    };
    targets = {
      agents.allSkills = true;
      explicit = {
        path = ".explicit/skills";
        skills = [ "hello" ];
      };
      disabled = {
        enable = false;
        allSkills = true;
        path = "../invalid";
        skills = [ "missing" ];
      };
    };
  };
  allSelected = makeHome allSettings;
  allEmpty = makeHome {
    enable = true;
    targets.agents.allSkills = true;
  };
  empty = makeHome { enable = true; };
  disabled = makeHome {
    enable = false;
    root = "/does-not-exist";
  };
  tests = [
    (valid { })
    (valid { enable = true; })
    (disabled.config.home.extraDependencies == [ ])
    (empty.config.home.extraDependencies == [ ])
    (empty.config.programs.skillset.targets == { })
    (lib.all (
      name: builtins.hasAttr "${expectedPaths.${name}}/hello" presetHomes.${name}.config.home.file
    ) (builtins.attrNames expectedPaths))
    (!(builtins.hasAttr ".cursor/skills/hello" overridden.config.home.file))
    (!(builtins.hasAttr ".claude/skills/hello" overridden.config.home.file))
    (
      !(builtins.hasAttr ".claude/skills/hello"
        (makeHome (base // { targets.claude = { }; })).config.home.file
      )
    )
    (valid {
      enable = true;
      targets.agents.allSkills = true;
    })
    (
      !(builtins.hasAttr ".agents/skills/hello"
        (makeHome (base // { targets.agents = { }; })).config.home.file
      )
    )
    (builtins.hasAttr ".agents/skills/hello"
      (makeHome (withTarget {
        allSkills = true;
      })).config.home.file
    )
    (
      !(valid (withTarget {
        allSkills = true;
        skills = [ "hello" ];
      }))
    )
    (!(builtins.hasAttr ".explicit/skills/helper" allSelected.config.home.file))
    (!(builtins.hasAttr "../invalid/extra" allSelected.config.home.file))
    (valid (withTarget {
      enable = false;
      path = "../invalid";
      skills = [ "absent" ];
    }))
    (
      !(valid (
        base
        // {
          skills.hello = {
            path = hello;
            livePath = "/nix/store/no-live";
          };
        }
      ))
    )
    (
      !(valid (
        base
        // {
          skills.hello = {
            path = hello;
            livePath = "relative/hello";
          };
        }
      ))
    )
    (builtins.hasAttr ".agents/skills/hello" selected.config.home.file)
    (builtins.hasAttr "Library/Application Support/example/skills/hello" selected.config.home.file)
    (!(builtins.hasAttr "../invalid/missing" selected.config.home.file))
    (toString selected.config.home.file.".agents/skills/hello".source == toString hello)
  ];
in
assert lib.assertMsg (lib.all (result: result) tests) "skillset module evaluation tests failed";
pkgs.runCommand "skillset-module-tests"
  {
    nativeBuildInputs = [ pkgs.python3 ];
    validations =
      selected.config.home.extraDependencies
      ++ rooted.config.home.extraDependencies
      ++ live.config.home.extraDependencies
      ++ generated.config.home.extraDependencies
      ++ stringPath.config.home.extraDependencies
      ++ allSelected.config.home.extraDependencies
      ++ allEmpty.config.home.extraDependencies
      ++ presets.config.home.extraDependencies
      ++ overridden.config.home.extraDependencies
      ++ presetHomes.codex.config.home.extraDependencies;
  }
  ''
    python3 -B - ${selected.config.home-files} ${rooted.config.home-files} ${live.config.home-files} <<'PY'
    from pathlib import Path
    import sys
    selected, rooted, live = map(Path, sys.argv[1:])
    for home in (selected, rooted):
        skill = home / ".agents/skills/hello"
        assert skill.is_symlink()
        assert str(skill.resolve()).startswith("/nix/store/")
        assert "Hello from a declared skill." in (skill / "SKILL.md").read_text()
    assert (selected / "Library/Application Support/example/skills/hello/SKILL.md").is_file()
    assert (live / ".agents/skills/hello").resolve() == Path("/tmp/skillset-live-fixture/hello").resolve()
    PY
    test -f ${generated.config.home-files}/.agents/skills/hello/SKILL.md
    test -f ${stringPath.config.home-files}/.agents/skills/hello/SKILL.md
    for name in hello helper extra; do
      test -L ${allSelected.config.home-files}/.agents/skills/"$name"
      test -f ${allSelected.config.home-files}/.agents/skills/"$name"/SKILL.md
    done
    test -f ${allSelected.config.home-files}/.explicit/skills/hello/SKILL.md
    test ! -e ${allEmpty.config.home-files}/.agents/skills
    for path in ${lib.escapeShellArgs (lib.unique (builtins.attrValues expectedPaths))}; do
      test -L ${presets.config.home-files}/"$path"/hello
      test -f ${presets.config.home-files}/"$path"/hello/SKILL.md
    done
    test -f ${presetHomes.codex.config.home-files}/.agents/skills/hello/SKILL.md
    test -f ${overridden.config.home-files}/.config/custom-claude/skills/hello/SKILL.md
    test -f ${overridden.config.home-files}/.work-agent/skills/hello/SKILL.md
    touch "$out"
  ''
