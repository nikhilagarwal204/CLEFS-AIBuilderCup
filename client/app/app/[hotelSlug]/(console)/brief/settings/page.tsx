import SettingsForm from "./SettingsForm";
import WaContacts from "./WaContacts";

/* /brief/settings: delivery (time, tiers, recipients, channels) and the
   WhatsApp opt-in list. Readers see both; brief.manage changes delivery. */
export default function BriefSettingsPage() {
  return (
    <>
      <SettingsForm />
      <WaContacts />
    </>
  );
}
