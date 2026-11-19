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
        ~/.config/kvn/config.json. The subscription URL and VPS link(s) are
        secrets and are NOT set here: put them into ~/.config/kvn/sub_url
        and ~/.config/kvn/vps_url.
      '';
    };

    vps.autoStart = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = ''
        Whether to start kvn-vps automatically on login. Regardless of this
        option, the kvn-vps user unit is installed whenever services.kvn.enable
        is true so `kvn vps start/stop/restart` works on demand.
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

    systemd.user.services.kvn-vps = {
      Unit = {
        Description = "kvn-vps: local sing-box proxy for a standalone VPS";
        ConditionPathExists = "%h/.config/kvn/vps_url";
      };
      Service = {
        ExecStart = "${lib.getExe cfg.package} vps run";
        Restart = "always";
        RestartSec = 5;
        Environment = [ "PYTHONUNBUFFERED=1" ];
      };
      Install = lib.mkIf cfg.vps.autoStart {
        WantedBy = [ "default.target" ];
      };
    };
  };
}
