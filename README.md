# RL Data Platform

A working slice of a platform for sourcing AI training data from many vendors: work is split across vendors, every vendor is measured the same way, and abuse is caught, decided on by a person, and cleaned out of the data.

Built in response to Anthropic's [Full-Stack Software Engineer, Reinforcement Learning](docs/POSTING.md) posting.

## Read first

| Document | What it covers |
|---|---|
| [PRD](docs/PRD.md) | The problem, who it serves, goals, capabilities and product decisions |
| [Design](docs/DESIGN.md) | Requirements, architecture and key decisions ([diagram](docs/diagrams/architecture.png)) |
| [Decisions](docs/DECISIONS.md) | Every decision, sorted by how hard it is to undo |
| [Build plan](docs/BUILD.md) | What this build covers, milestones and what's left out |
| [Glossary](docs/GLOSSARY.md) | Terms, for readers outside RL or the data-vendor market |

## Run it

Needs Docker with Compose.

```sh
make up      # build and start Postgres, the API and the web app
make test    # run the backend tests against a separate test database
make down    # stop (make reset also deletes the database)
```

Then open http://localhost:5173 and pick a demo user to sign in as. The API is at http://localhost:8000 (interactive docs at `/docs`).

## Status

The build is in progress. Milestones and their status are in the [build plan](docs/BUILD.md#4-milestones).
