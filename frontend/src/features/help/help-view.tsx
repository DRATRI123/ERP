import { ArrowRightIcon, InfoIcon, LightbulbIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";

import { HELP_BY_ID, type HelpTopic } from "./content";

/** One guide: summary, numbered steps per task, notes, tips and related guides. */
export function HelpView({ topic, onOpen }: { topic: HelpTopic; onOpen?: (id: string) => void }) {
  return (
    <article className="space-y-5">
      <p className="text-sm leading-relaxed text-muted-foreground">{topic.summary}</p>
      {topic.sections.map((s) => (
        <section key={s.heading} className="space-y-2">
          <h3 className="text-sm font-semibold">{s.heading}</h3>
          <ol className="space-y-1.5">
            {s.steps.map((step, i) => (
              <li key={i} className="flex gap-2.5 text-sm leading-relaxed">
                <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-primary-soft text-[11px] font-semibold text-primary">{i + 1}</span>
                <span>{step}</span>
              </li>
            ))}
          </ol>
          {s.note && <p className="flex gap-2 rounded-md bg-blue-soft/60 px-3 py-2 text-sm"><InfoIcon className="mt-0.5 size-4 shrink-0 text-blue" />{s.note}</p>}
        </section>
      ))}
      {topic.tips?.length ? (
        <section className="space-y-1.5 rounded-lg border border-dashed p-3">
          {topic.tips.map((t) => <p key={t} className="flex gap-2 text-sm"><LightbulbIcon className="mt-0.5 size-4 shrink-0 text-amber-600" />{t}</p>)}
        </section>
      ) : null}
      {topic.related?.length && onOpen ? (
        <section className="space-y-2">
          <h3 className="text-xs font-semibold tracking-wider text-muted-foreground uppercase">Related guides</h3>
          <div className="flex flex-wrap gap-2">
            {topic.related.map((id) => HELP_BY_ID.get(id)).filter(Boolean).map((t) => (
              <button key={t!.id} type="button" onClick={() => onOpen(t!.id)}>
                <Badge variant="outline" className="cursor-pointer gap-1 hover:bg-accent">{t!.title}<ArrowRightIcon className="size-3" /></Badge>
              </button>
            ))}
          </div>
        </section>
      ) : null}
    </article>
  );
}
