import { Shell } from "@/components/shell";
import { Card, CardHeader, PageHeader } from "@/components/ui";
import { BASE } from "@/lib/api";

const INSTALL: [string, string][] = [
  ["Linux and macOS", "curl -fsSL API/cli/install.sh | sh"],
  ["Windows (PowerShell)", "irm API/cli/install.ps1 | iex"],
];

const STEPS: [string, string, string][] = [
  ["Sign in", "dockra login", "Opens the browser once; the session is stored on this machine."],
  ["Initialize", "dockra init myapp", "Creates dockra.json with the application ID."],
  ["Deploy", "dockra deploy", "Packs and uploads the folder and streams build events to the terminal."],
];

const COMMANDS: [string, string][] = [
  ["dockra domain add app.example.com", "Add a custom domain and issue SSL"],
  ["dockra env set DEBUG=0", "Set an environment variable on the running application"],
  ["dockra logs --follow", "Live application logs"],
  ["dockra restart", "Restart the containers"],
  ["dockra deployments", "List releases"],
  ["dockra rollback v2", "Roll back to a previous release"],
  ["dockra db create myapp-db --engine postgres", "Create a PostgreSQL database"],
  ["dockra db link myapp-db", "Write DATABASE_URL into the application"],
];

const MANIFEST = `{
  "app": "myapp",
  "platform": "django",
  "port": 8000,
  "pythonVersion": "3.12",
  "packages": ["ffmpeg"],
  "disks": [{ "name": "media", "mountTo": "/app/media" }],
  "cron": ["* * * * * python manage.py run_due_jobs"]
}`;

export default function DocsPage() {
  return (
    <Shell>
      <PageHeader title="Docs" subtitle="From a project folder to a running application in three commands" />
      <Card className="mb-4">
        <CardHeader
          title="Install dockra"
          icon="terminal"
          description="Installed from this server, so it also works on isolated networks. Update with dockra upgrade."
        />
        <ul className="divide-y divide-line/60">
          {INSTALL.map(([label, cmd]) => (
            <li key={label} className="flex flex-col gap-1 px-5 py-3 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
              <code className="font-mono text-[13px] text-brand">{cmd.replace("API", BASE)}</code>
              <span className="text-xs text-muted">{label}</span>
            </li>
          ))}
        </ul>
      </Card>
      <div className="grid gap-4 md:grid-cols-3">
        {STEPS.map(([title, cmd, text], index) => (
          <Card key={cmd} className="p-5">
            <div className="flex items-center gap-2.5">
              <span className="grid h-7 w-7 place-items-center rounded-full bg-accent/10 text-xs font-bold text-brand">{index + 1}</span>
              <span className="text-sm font-semibold">{title}</span>
            </div>
            <code className="mt-4 block rounded-lg bg-field px-3 py-2.5 font-mono text-[13px] text-brand">$ {cmd}</code>
            <p className="mt-3 text-xs leading-6 text-muted">{text}</p>
          </Card>
        ))}
      </div>

      <div className="mt-5 grid gap-5 lg:grid-cols-5">
        <Card className="lg:col-span-3">
          <CardHeader title="Everyday commands" icon="terminal" />
          <ul className="divide-y divide-line/60">
            {COMMANDS.map(([cmd, text]) => (
              <li key={cmd} className="flex flex-col gap-1 px-5 py-3 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
                <code className="font-mono text-[13px] text-fg">{cmd}</code>
                <span className="text-xs text-muted">{text}</span>
              </li>
            ))}
          </ul>
        </Card>
        <Card className="lg:col-span-2">
          <CardHeader title="dockra.json" icon="book" description="Every field except app is optional." />
          <pre className="overflow-auto p-5 font-mono text-xs leading-6 text-fg-2">{MANIFEST}</pre>
          <div className="space-y-2 border-t border-line/60 px-5 py-4 text-xs leading-6 text-muted">
            <p>Ignore rules: in each folder the first file found among .dockraignore, .liaraignore, .dockerignore and .gitignore is used.</p>
            <p>To build with the project's own Dockerfile, set "platform": "docker".</p>
          </div>
        </Card>
      </div>
    </Shell>
  );
}
