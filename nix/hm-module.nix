self:
{ config, lib, pkgs, ... }:
let
  cfg = config.services.kvn;
  json = pkgs.formats.json { };
in
{
  options.services.kvn = {
    enable = lib.mkEnableOption "kvn, a BlancVPN watchdog (sing-box proxy with auto failover)";

    package = lib.mkOption {
      type = lib.types.package;
      default = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
      defaultText = lib.literalExpression "kvn.packages.\${system}.default";
    };

    settings = lib.mkOption {
      type = json.type;
      default = { };
      example = { port = 1080; bypass_russia = false; };
      description = ''
        Overrides of the built-in defaults, written to
        ~/.config/kvn/config.json. The subscription URL is a secret
        and is NOT set here: put it into ~/.config/kvn/sub_url.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    home.packages = [ cfg.package ];

    xdg.configFile."kvn/config.json" = lib.mkIf (cfg.settings != { }) {
      source = json.generate "kvn-config.json" cfg.settings;
    };

    systemd.user.services.kvn = {
      Unit = {
        Description = "kvn: sing-box proxy with auto failover";
        ConditionPathExists = "%h/.config/kvn/sub_url";
      };
      Service = {
        ExecStart = "${lib.getExe cfg.package} run";
        Restart = "always";
        RestartSec = 10;
        Environment = [ "PYTHONUNBUFFERED=1" ];
      };
      Install.WantedBy = [ "default.target" ];
    };
  };
}
