# Glossary

Terms used in [`POSTING.md`](POSTING.md), [`PRD.md`](PRD.md) and [`DESIGN.md`](DESIGN.md), defined the way this project uses them. Where the posting uses a term, the posting's wording is quoted so readers can check our reading against it.

## Parties and roles

| Term | Meaning here | In the posting |
|---|---|---|
| **Anthropic** | The AI lab that needs training data and runs the platform. The buyer in the data market | |
| **Platform** | The single, Anthropic-run system this project describes. Vendors use it through their own workspaces | *"web platforms for RL environment creation, management, and quality review"* |
| **Vendor** | An organization Anthropic contracts to supply human work, built environments, or both. A middleman between Anthropic's researchers (demand) and the workers or code that do the work (supply). Examples: Surge AI, Mercor, Scale AI, Turing, Mechanize | *"external vendors"*; *"external partners"* |
| **Workforce vendor** | A vendor that recruits, vets, pays and manages workers, and sells their output (labels, judgments, expert answers). Often runs a separate worker-facing brand: DataAnnotation.tech is reported to be Surge AI's | Implied by *"thousands of data labelers"* and *"many vendors"* |
| **Environment vendor** | A vendor that builds and maintains RL environments. Some workforce vendors are also environment vendors | *"let external partners create, submit, and iterate on training environments"* |
| **Vendor capability** | Which kinds of work a vendor supplies (human work, environments, or both) and in which domains (coding, law, medicine…). Decides which projects a vendor can receive | |
| **Workspace** | A vendor's own area of the platform, holding its users, its work and its data. Other vendors can't see into it. Engineering term: *tenant* | *"multi-tenant platforms"* |
| **Researcher** | An Anthropic RL researcher. Defines projects, reviews environments and data, and watches training. Works across all workspaces | *"RL researchers"* |
| **Data ops** | Anthropic staff who choose and manage vendors: onboarding, allocation, performance and abuse cases | *"data operations, and vendor management"* |
| **Vendor admin** | A vendor's manager on the platform. Manages the vendor's users, monitors its quality and handles abuse among its workers | |
| **Vendor reviewer** | A vendor's own QA staff, who check workers' output before it reaches Anthropic. Not every vendor has them | |
| **Worker** | A person employed or contracted by a vendor to do human work. Covers labelers and experts | *"thousands of human experts around the world"* |
| **Labeler** | A worker doing general labeling tasks; often non-technical | *"non-technical labelers"* |
| **Expert** | A worker hired for domain knowledge (a lawyer, a doctor, a senior engineer) | *"distill the knowledge of thousands of human experts"* |
| **Environment builder** | A vendor's engineer who creates and submits environments | |

## Work and data

| Term | Meaning here | In the posting |
|---|---|---|
| **Project** | A unit of work defined by a researcher: what to produce, instructions, quality bar, volume and deadline. Can be split across several vendors | |
| **Task** | One unit of human work inside a project, such as "compare these two answers". Engineering term: *item* | *"many task types"* |
| **Submission** | A worker's answer to a task, or a vendor's upload of an environment version | *"create, submit, and iterate"* |
| **Allocation** | How much of a project each vendor receives | |
| **Human data** | Data produced by workers: labels, ratings, comparisons, written answers, reviews of model transcripts | *"human data collection"* |
| **Labeling workflow** | The steps a task goes through: assignment, answer, review, acceptance or rejection | *"labeling workflows"* |
| **Dataset** | An exported, quality-checked batch of human data that training uses | |

## Environments and training

| Term | Meaning here | In the posting |
|---|---|---|
| **RL** | Reinforcement learning: the model attempts tasks, gets a score (reward), and is trained to do better | *"Reinforcement Learning"* |
| **Environment** | A packaged practice task for the model: instructions, the software it acts in (a container), and a grader. TechCrunch: *"training grounds that simulate what an AI agent would be doing in a real software application"* | *"training environments"*; *"RL environment creation"* |
| **Environment version** | One immutable snapshot of an environment. Training always refers to an exact version | *"versioning"* |
| **Grader** | The code that scores an attempt at an environment, for example a test suite | |
| **Reward signal** | The score a grader (or human judgment) gives an attempt. Training pushes the model toward higher rewards, so a wrong reward teaches a wrong lesson | *"robust reward signals"*; *"reward signal integrity"* |
| **Reward hacking** | The model earning a high reward without doing the task, by exploiting a flaw in the grader. Like a student editing the answer key | *"catch reward hacks"*; *"reward hacking"* |
| **Validation** | Automated checks on an environment before training uses it: the reference solution passes, a no-op fails, repeated runs agree | *"validate correctness and difficulty calibration"* |
| **Difficulty calibration** | Measuring how hard an environment is (for example, how often a baseline model solves it) so training gets a useful mix | *"difficulty calibration"* |
| **Training run** | One training job that uses environments and datasets | *"training run health"* |
| **Episode** | One attempt by the model at one environment during a training run. Produces a reward and a transcript | |
| **Transcript** | The full record of an episode: what the model saw and did | |
| **Quarantine** | Removing an environment version or dataset from training until it's reviewed | |

## Quality, performance and abuse

| Term | Meaning here | In the posting |
|---|---|---|
| **QA** | Quality assurance: the checks that decide whether work is accepted | *"quality assurance systems"*; *"QA"* |
| **Gold task** | A task with an answer known in advance, mixed in unannounced to measure a worker's accuracy | |
| **Agreement** | How often workers give the same answer to the same task. Low agreement means unclear instructions or unreliable workers | |
| **Acceptance rate** | The share of a vendor's (or worker's) submissions that pass QA | |
| **Vendor performance** | How a vendor does on quality, volume, cost and turnaround, per project and over time | |
| **Scorecard** | A view of one vendor's performance over time, comparable across vendors | |
| **Throughput** | Accepted work per unit of time | *"data collection progress"* |
| **Turnaround** | Time from a project or task being issued to accepted work coming back | *"a loop that closes in hours and days"* |
| **Abuse** | Deliberate behavior that makes data look better, larger or more human than it is, or that misuses the platform. Happens at two levels: worker abuse and vendor abuse | |
| **Worker abuse** | Abuse by individual workers, for example AI-generated answers where human judgment is required, answering without reading, multiple or shared accounts, colluding to game agreement, false claims of expertise, leaking tasks. Vendors detect and act first | |
| **Vendor abuse** | Abuse by a vendor as an organization, for example padding volume with low-quality work, re-admitting banned workers, subcontracting without saying so, submitting environments with gameable graders or copied benchmark content. Anthropic detects and acts | |
| **Lineage** | The trace from a piece of training data back to the task, worker (pseudonymous), vendor, environment version and reviewer it came from. Used to clean up after abuse is found | *"audit trails"* |
