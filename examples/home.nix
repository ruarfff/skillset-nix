{
  home.username = "example";
  home.homeDirectory = "/Users/example";
  home.stateVersion = "24.11";

  programs.skillset = {
    enable = true;
    root = ./skills;
    targets.agents = {
      path = ".agents/skills";
      skills = [ "hello" ];
    };
  };
}
