# Connect to MemoryDB via Bastion

All connection parameters live in [config.yaml](config.example.yaml). The examples below use environment variables that mirror the YAML keys — you can `source` them or substitute literals as needed.

## Prerequisites

| Setting | `config.yaml` key | Env var |
|---|---|---|
| SSH key path | `bastion.ssh_key` | `BASTION_SSH_KEY` |
| Bastion public IP | `bastion.ip` | `BASTION_IP` |
| Bastion user | `bastion.user` | `BASTION_USER` |
| MemoryDB endpoint | `memorydb.host` | `MEMORYDB_HOST` |
| MemoryDB port | `memorydb.port` | `MEMORYDB_PORT` |

## Option 1: Port Forward (use local tools)

Forward port 6379 from the bastion to your machine:

```bash
ssh -i "$BASTION_SSH_KEY" \
  -f -N -L "$MEMORYDB_PORT:$MEMORYDB_HOST:$MEMORYDB_PORT" \
  "$BASTION_USER@$BASTION_IP"
```

Then use any local Redis client:

```bash
redis-cli -p "$MEMORYDB_PORT" --tls
PING
KEYS *
```

To stop the forward:

```bash
kill $(lsof -ti :"$MEMORYDB_PORT")
```

## Option 2: SSH into Bastion

```bash
ssh -i "$BASTION_SSH_KEY" "$BASTION_USER@$BASTION_IP"
```

Once inside, use `redis6-cli`:

```bash
redis6-cli -h "$MEMORYDB_HOST" -p "$MEMORYDB_PORT" --tls
```

## Option 3: Python from Bastion

```bash
ssh -i "$BASTION_SSH_KEY" "$BASTION_USER@$BASTION_IP"
python3 -c "
from config import cfg
import redis
r = redis.Redis(
    host=cfg.memorydb.host,
    port=cfg.memorydb.port,
    ssl=cfg.memorydb.tls,
    decode_responses=True,
)
print(r.info('server'))
"
```

## Sample Data

| Key | Type |
|-----|------|
| `greeting:1` | String (JSON) |
| `user:100:name` / `user:100:email` | String |
| `user:200:name` / `user:200:email` | String |
| `session:abc123` | String (JSON) |
| `queue:tasks` | List |
| `tags:redis` | Set |
| `leaderboard` | Sorted Set |
| `locations` | Geo |
