import { cn } from "@/lib/utils";
import type { SettingValue } from "@/store/snapshot";
import {
  ADAPTIVE_LEVEL,
  ADAPTIVE_MODE,
  PRESS_BITS_MIN,
  PRESS_HOLD_BITS,
  SWITCH_OFF,
  SWITCH_ON,
  type Def,
  type Option,
} from "./catalog";
import { Segmented } from "./Segmented";
import { SettingRow } from "./SettingRow";
import { Slider } from "./Slider";
import { Switch } from "./Switch";
import { useSetting, type Setting } from "./useSetting";
import { Check } from "./icons";
import { DISABLED, FOCUS } from "./shared";

export type Settings = Record<string, SettingValue>;

export function SwitchRow({ def, settings }: { def: Def; settings: Settings }) {
  const ctl = useSetting(def, settings);
  return (
    <SettingRow label={def.label} ctl={ctl}>
      {(labelId) => (
        <Switch
          checked={ctl.value === SWITCH_ON}
          onChange={(on) => ctl.set(on ? SWITCH_ON : SWITCH_OFF)}
          labelledBy={labelId}
          disabled={ctl.waiting}
          busy={ctl.pending}
        />
      )}
    </SettingRow>
  );
}

export function ChoiceRow({
  def,
  ctl,
  options,
  label = def.label,
  size,
  layout,
}: {
  def: Def;
  ctl: Setting;
  options: readonly Option[];
  label?: string;
  size?: "md" | "lg";
  layout?: "inline" | "block" | "quad";
}) {
  return (
    <SettingRow
      label={label}
      ctl={ctl}
      block={layout === "block" || layout === "quad"}
    >
      {(labelId) => (
        <Segmented
          options={options}
          value={ctl.value}
          onChange={ctl.set}
          labelledBy={labelId}
          disabled={ctl.waiting}
          busy={ctl.pending}
          size={size}
          layout={layout}
        />
      )}
    </SettingRow>
  );
}

interface PairRowSpec {
  label: string;
  index: number;
  options: readonly Option[];
}

/** A width-2 setting shown as one segmented row per element; a change rewrites only its element. */
export function PairRows({
  def,
  settings,
  rows,
}: {
  def: Def;
  settings: Settings;
  rows: readonly PairRowSpec[];
}) {
  const ctl = useSetting(def, settings);
  const pair = Array.isArray(ctl.value) ? (ctl.value as unknown[]) : [];
  return (
    <>
      {rows.map((row, i) => (
        <SettingRow
          key={row.label}
          label={row.label}
          ctl={ctl}
          showCaption={i === rows.length - 1}
        >
          {() => (
            <Segmented
              options={row.options}
              value={pair[row.index]}
              onChange={(v) => {
                const next = [pair[0], pair[1]];
                next[row.index] = v;
                ctl.set(next);
              }}
              ariaLabel={`${def.label}: ${row.label}`}
              disabled={ctl.waiting}
              busy={ctl.pending}
            />
          )}
        </SettingRow>
      ))}
    </>
  );
}

const bits = (n: number) => [1, 2, 4, 8].filter((b) => (n & b) !== 0).length;

export function BitmaskRow({
  def,
  settings,
}: {
  def: Def;
  settings: Settings;
}) {
  const ctl = useSetting(def, settings);
  const mask = typeof ctl.value === "number" ? ctl.value : 0;
  const count = bits(mask);
  return (
    <SettingRow
      label={def.label}
      ctl={ctl}
      block
      hint={
        !ctl.waiting && count <= PRESS_BITS_MIN
          ? "Choose at least two"
          : undefined
      }
    >
      {(labelId) => (
        <div className="@container">
          <div
            role="group"
            aria-labelledby={labelId}
            aria-busy={ctl.pending ? "true" : undefined}
            className="grid grid-cols-2 gap-1.5 @[34rem]:grid-cols-4"
          >
            {PRESS_HOLD_BITS.map((bit) => {
              const on = (mask & bit.value) !== 0;
              const locked = on && count <= PRESS_BITS_MIN;
              return (
                <button
                  key={bit.value}
                  type="button"
                  role="checkbox"
                  aria-checked={on}
                  aria-disabled={locked ? "true" : undefined}
                  disabled={ctl.waiting}
                  onClick={() => !locked && ctl.set(mask ^ bit.value)}
                  className={cn(
                    "m-0 inline-flex min-h-7 items-center justify-center gap-1 rounded-[7px] border-0 px-2.5 text-[12px] leading-tight",
                    "motion-safe:transition-colors motion-safe:duration-150",
                    on
                      ? "bg-accent/12 font-semibold text-foreground ring-1 ring-accent/50 ring-inset"
                      : "bg-foreground/6 text-foreground/80 hover:bg-foreground/10 hover:text-foreground active:bg-foreground/15",
                    locked && "cursor-not-allowed opacity-70",
                    FOCUS,
                    DISABLED,
                  )}
                >
                  <span
                    className={cn(
                      "grid size-3 place-items-center text-accent",
                      !on && "invisible",
                    )}
                  >
                    <Check />
                  </span>
                  {bit.label}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </SettingRow>
  );
}

export function SliderRow({
  def,
  ctl,
  min,
  max,
  step,
  ticks,
  readout,
  inactive,
  inactiveHint,
}: {
  def: Def;
  ctl: Setting;
  min: number;
  max: number;
  step: number;
  ticks?: string[];
  readout?: boolean;
  inactive?: boolean;
  inactiveHint?: string;
}) {
  const shown = typeof ctl.value === "number" ? ctl.value : min;
  return (
    <SettingRow
      label={def.label}
      ctl={ctl}
      block
      hint={inactive && !ctl.waiting ? inactiveHint : undefined}
      trailing={
        readout ? <ReadoutSlot value={ctl.waiting ? null : shown} /> : undefined
      }
    >
      {(labelId) => (
        <>
          <Slider
            min={min}
            max={max}
            step={step}
            value={ctl.value}
            onCommit={ctl.set}
            labelledBy={labelId}
            disabled={ctl.waiting || inactive}
            busy={ctl.pending}
          />
          {ticks && (
            <div
              aria-hidden="true"
              className="flex justify-between text-[11px] leading-normal text-(color:--muted-ink) tabular-nums"
            >
              {ticks.map((t) => (
                <span key={t}>{t}</span>
              ))}
            </div>
          )}
        </>
      )}
    </SettingRow>
  );
}

function ReadoutSlot({ value }: { value: number | null }) {
  return (
    <span className="text-(color:--muted-ink) tabular-nums">
      {value === null ? "—" : value}
    </span>
  );
}

export function AdaptiveRow({
  settings,
  mode,
}: {
  settings: Settings;
  mode: unknown;
}) {
  const ctl = useSetting(ADAPTIVE_LEVEL, settings);
  return (
    <SliderRow
      def={ADAPTIVE_LEVEL}
      ctl={ctl}
      min={0}
      max={100}
      step={50}
      ticks={["0", "50", "100"]}
      inactive={mode !== ADAPTIVE_MODE}
      inactiveHint="Used in Adaptive mode"
    />
  );
}
