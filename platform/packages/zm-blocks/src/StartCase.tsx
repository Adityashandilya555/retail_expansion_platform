// "New <case>" (S3-02): a flow's start form in a modal. Submitting starts the case
// (`POST /app/cases`) and opens its page; the API sends the form only to users who may start the
// flow, so the button shows only for them.
import { Button, Modal, useMatches } from "@mantine/core";
import { useQueryClient } from "@tanstack/react-query";
import { ZmForm, type FormSchema, type ZmFormProps } from "@zm/forms";
import { notifyError } from "@zm/ui";
import { useState } from "react";
import { useNavigate } from "react-router";
import { useBlocksApi } from "./api";
import type { AppUi } from "./registry";

/** Extra `ZmForm` props are forwarded to the form (S3-03 adds `loadPeople`). */
export type StartCaseProps = Omit<ZmFormProps, "schema" | "layout" | "onSubmit"> & { flow: AppUi["flows"][number] };

/** "New <case>" (e.g. "New Site") and its start form, full screen below `sm`; nothing when the
 * flow has no start form. While a submit is in flight the modal can't be closed, so its answer
 * (the case page, or an error) never lands after the person thinks they cancelled. */
export function StartCase({ flow, ...form }: StartCaseProps) {
  const api = useBlocksApi();
  const queries = useQueryClient();
  const navigate = useNavigate();
  const [opened, setOpened] = useState(false);
  const [sending, setSending] = useState(false);
  const fullScreen = useMatches({ base: true, sm: false });
  const start = flow.start_form;
  if (!start) return null;

  const title = `New ${flow.case_label}`;
  const submit: ZmFormProps["onSubmit"] = async (values) => {
    setSending(true);
    try {
      const { data, error, response } = await api.POST("/app/cases", { body: { flow: flow.key, values } });
      if (!data) {
        if (response.status === 422 && error?.errors) return error.errors;
        notifyError(error?.detail ?? `Couldn't start this ${flow.case_label.toLowerCase()}.`);
        return;
      }
      // the new case lists in every case table, and its first task is in someone's inbox
      void queries.invalidateQueries({ queryKey: ["app", "cases"] });
      void queries.invalidateQueries({ queryKey: ["app", "tasks"] });
      setOpened(false);
      navigate(`/app/cases/${encodeURIComponent(data.code)}`);
    } finally {
      setSending(false);
    }
  };
  return (
    <>
      <Button onClick={() => setOpened(true)}>{title}</Button>
      <Modal
        opened={opened}
        onClose={() => setOpened(false)}
        closeOnEscape={!sending}
        closeOnClickOutside={!sending}
        title={title}
        size="lg"
        fullScreen={fullScreen}
        closeButtonProps={{ "aria-label": "Close", disabled: sending }}
        // Mantine draws the dialog's header as <header>, which reads as a second page banner
        attributes={{ header: { role: "presentation" } }}
      >
        <ZmForm
          caseLabel={flow.case_label}
          {...form}
          schema={start.schema as FormSchema}
          layout={start.layout as ZmFormProps["layout"]}
          onSubmit={submit}
        />
      </Modal>
    </>
  );
}
