import { Alert, Box, Button, Fieldset, Grid, Group, Input, NumberInput, Paper, Radio, Select, SimpleGrid, Stack, Text, TextInput, Textarea, Title } from "@mantine/core";
import { useForm, type UseFormReturnType } from "@mantine/form";
import { useEffect, useId, useRef, useState } from "react";
import { formErrors, type FieldError, type FieldSchema, type FormSchema, type FormSection } from "./validate";

type Values = Record<string, unknown>;

export type ZmFormProps = {
  schema: FormSchema;
  layout: FormSection[];
  /** Initial values (read once: key the form by task id). */
  values?: Values;
  readOnly?: boolean;
  /** The flow's `case_label`, e.g. "Site": titles the panel of `x-zm-show` values. */
  caseLabel?: string;
  /** The `x-zm-show` values with their labels, keyed by field. */
  caseData?: Record<string, { label: string; value: unknown }>;
  /** Gets values that passed the schema (unanswered fields left out); resolves with the API's 422 errors, if any. */
  onSubmit: (values: Values) => Promise<FieldError[] | undefined> | Promise<void>;
};

// ponytail: reject-type by outcome id (the compiler's and both templates' id); flag it in x-zm-options if others appear.
const isReject = (id: string) => id === "reject";

const answered = (values: Values) => Object.fromEntries(Object.entries(values).filter(([, v]) => v !== "" && v !== null && v !== undefined));

/** `/field/sub` → `field` */
const fieldOf = (path: string) => (path.split("/")[1] ?? "").replaceAll("~1", "/").replaceAll("~0", "~");

function display(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (typeof v === "number") return new Intl.NumberFormat("en-IN").format(v);
  if (Array.isArray(v)) return v.map(display).join(", ");
  if (typeof v === "object") return "name" in v ? String(v.name) : JSON.stringify(v);
  return String(v);
}

function Unsupported({ name, label, type }: { name: string; label: string; type: string }) {
  useEffect(() => {
    if (import.meta.env.DEV) console.warn(`@zm/forms: ${name} is a ${type} field, unsupported in v1`);
  }, [name, type]);
  return (
    <Input.Wrapper label={label}>
      <Text size="sm" c="dimmed">
        {type} fields are unsupported in v1
      </Text>
    </Input.Wrapper>
  );
}

function Field({ name, field, form, required, readOnly }: { name: string; field: FieldSchema; form: UseFormReturnType<Values>; required: boolean; readOnly: boolean }) {
  const { value, onChange, error, onFocus, onBlur } = form.getInputProps(name);
  const type = field["x-zm-type"] ?? "text";
  const common = { label: field.title ?? name, withAsterisk: required, error, readOnly, onFocus, onBlur };
  const text = typeof value === "string" ? value : "";
  switch (type) {
    case "text":
      return <TextInput {...common} value={text} onChange={onChange} />;
    case "date":
      return <TextInput {...common} type="date" value={text} onChange={onChange} />;
    case "textarea":
      return <Textarea {...common} autosize minRows={3} value={text} onChange={onChange} />;
    case "number":
    case "currency":
      return (
        <NumberInput
          {...common}
          value={typeof value === "number" ? value : ""}
          onChange={onChange}
          {...(type === "currency" && { prefix: "₹ ", thousandSeparator: ",", thousandsGroupStyle: "lakh" as const })}
        />
      );
    case "select":
      return <Select {...common} clearable data={(field["x-zm-options"] ?? []).map((o) => ({ value: o.id, label: o.label }))} value={text || null} onChange={onChange} />;
    case "yes_no":
      return (
        <Radio.Group {...common} value={value === true ? "yes" : value === false ? "no" : null} onChange={(v) => onChange(v === "yes")}>
          <Group mt={4}>
            <Radio value="yes" label="Yes" />
            <Radio value="no" label="No" />
          </Group>
        </Radio.Group>
      );
    case "document":
      return <TextInput label={common.label} disabled value={display(value) === "—" ? "" : display(value)} placeholder="No file yet" description="Uploads arrive with Files" />;
    default:
      return <Unsupported name={name} label={common.label} type={type} />;
  }
}

/** A compiled form (#19): sections → rows → a 12-column grid (one column on phones), outcomes as buttons, Ajv checks before `onSubmit`. */
export function ZmForm({ schema, layout, values = {}, readOnly = false, caseLabel = "Case", caseData, onSubmit }: ZmFormProps) {
  const id = useId();
  const root = useRef<HTMLFormElement>(null);
  const form = useForm<Values>({ initialValues: values });
  const [pending, setPending] = useState<string | null>(null);
  const [formLevel, setFormLevel] = useState<string[]>([]);

  const props = schema.properties;
  const required = new Set(schema.required ?? []);
  const outcomeKey = Object.keys(props).find((k) => props[k]?.["x-zm-type"] === "outcome");
  const commentKey = outcomeKey?.replace(/_outcome$/, "_comment");
  const outcomes = (outcomeKey && props[outcomeKey]?.["x-zm-options"]) || [];
  const needsComment = ((schema.if as { properties?: Record<string, { enum?: string[] }> } | undefined)?.properties?.[outcomeKey ?? ""]?.enum ?? [])
    .map((o) => outcomes.find((x) => x.id === o)?.label.toLowerCase())
    .filter(Boolean);
  const rendered = new Set([...layout.flatMap((s) => s.rows.flat().map((c) => c.field)), ...(commentKey ? [commentKey] : [])]);
  const show = schema["x-zm-show"] ?? [];

  function showErrors(errors: FieldError[]) {
    const byField: Record<string, string> = {};
    const other: string[] = [];
    for (const e of errors) {
      const f = fieldOf(e.path);
      if (rendered.has(f) && props[f]) byField[f] ??= e.message;
      else other.push(props[f]?.title ? `${props[f].title}: ${e.message}` : e.message);
    }
    form.setErrors(byField);
    setFormLevel(other);
    const first = [...(root.current?.querySelectorAll<HTMLElement>("[data-zm-field]") ?? [])].find((el) => (el.dataset.zmField ?? "") in byField);
    first?.querySelector<HTMLElement>("input:not([type=hidden]), textarea")?.focus();
  }

  async function submit(outcome?: string) {
    const data = answered({ ...form.getValues(), ...(outcomeKey && outcome && { [outcomeKey]: outcome }) });
    const errors = formErrors(schema, data);
    if (errors.length) return showErrors(errors);
    setFormLevel([]);
    setPending(outcome ?? "submit");
    try {
      const server = await onSubmit(data);
      if (Array.isArray(server) && server.length) showErrors(server);
    } finally {
      setPending(null);
    }
  }

  return (
    <form
      ref={root}
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        if (!outcomeKey && !readOnly) void submit();
      }}
    >
      <Stack>
        {show.length > 0 && (
          <Paper withBorder p="md" component="section" aria-labelledby={`${id}-case`}>
            <Title order={3} size="h5" id={`${id}-case`}>
              {caseLabel} data
            </Title>
            <SimpleGrid component="dl" cols={{ base: 1, sm: 2 }} mt="sm" m={0}>
              {show.map((k) => (
                <div key={k}>
                  <Text component="dt" size="xs" c="dimmed">
                    {caseData?.[k]?.label ?? k}
                  </Text>
                  <Text component="dd" size="sm" m={0}>
                    {display(caseData?.[k]?.value)}
                  </Text>
                </div>
              ))}
            </SimpleGrid>
          </Paper>
        )}

        {layout.map((section) => (
          <Fieldset key={section.key} legend={section.title}>
            <Stack gap="sm">
              {section.rows.map((row, i) => (
                <Grid key={i}>
                  {row.map((cell) => {
                    const field = props[cell.field]; // absent when the field is hidden
                    return field ? (
                      <Grid.Col key={cell.field} span={{ base: 12, sm: cell.span ?? 12 }} data-zm-field={cell.field}>
                        <Field name={cell.field} field={field} form={form} required={required.has(cell.field)} readOnly={readOnly} />
                      </Grid.Col>
                    ) : null;
                  })}
                </Grid>
              ))}
            </Stack>
          </Fieldset>
        ))}

        {commentKey && props[commentKey] && (
          <Box data-zm-field={commentKey}>
            <Field name={commentKey} field={props[commentKey]} form={form} required={false} readOnly={readOnly} />
            {needsComment.length > 0 && !readOnly && (
              <Text size="xs" c="dimmed" mt={4}>
                Required to {needsComment.join(" or ")}
              </Text>
            )}
          </Box>
        )}

        {formLevel.length > 0 && (
          <Alert color="red" title="Couldn't submit">
            {formLevel.join(" · ")}
          </Alert>
        )}

        {!readOnly && (
          <Group>
            {outcomeKey ? (
              outcomes.map((o, i) => (
                <Button
                  key={o.id}
                  variant={i === 0 ? "filled" : isReject(o.id) ? "light" : "default"}
                  color={isReject(o.id) ? "red" : undefined}
                  loading={pending === o.id}
                  disabled={pending !== null}
                  onClick={() => void submit(o.id)}
                >
                  {o.label}
                </Button>
              ))
            ) : (
              <Button type="submit" loading={pending !== null}>
                Submit
              </Button>
            )}
          </Group>
        )}
      </Stack>
    </form>
  );
}
