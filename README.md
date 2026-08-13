# Plugin Foothold
With this plugin, you can display persistent campaign status embeds for your Foothold mission saves.

## Configuration
As Foothold is an optional plugin, you need to activate it in `main.yaml` first like so:
```yaml
# config/main.yaml
opt_plugins:
  - foothold
```

The configuration is stored in `config/plugins/foothold.yaml` and is quite easy. You define a server section matching your instance name and the channel where the embed should be displayed. You can also define how often the embed should update.

```yaml
# config/plugins/foothold.yaml
DEFAULT:
  update_interval: 120        # interval in seconds when the embed should update (default = 120)

DCS.dcs_serverrelease:
  lua_file_name: "Foothold_Ranks.lua"
  channel: 1122334455667788      # channel where the stats are uploaded (WARNING: this channel will get its messages often cleared)
  update_interval: 120
```

The plugin reads the Foothold save file from the instance mission saves folder:
`{instance.home}\Missions\Saves\<lua_file_name>`.

Every parameter has a default value, which means you do not need to specify `update_interval` unless you want a different polling interval.

This works for Foothold and similar mission save files.

## Bot permissions

The bot needs the following permissions in the target channel:

- View Channel / Read Messages
- Send Messages
- Embed Links
- Read Message History
- Manage Messages

These permissions allow the plugin to publish embeds and remove old status messages cleanly.

## Credits
Credits to Leka for keeping the cool Foothold missions.
