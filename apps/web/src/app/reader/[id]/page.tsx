import AppShell from '@/components/AppShell';

export default async function DocumentPage({params}: {params: Promise<{id: string}>}) {
  const {id} = await params;
  return <AppShell initialId={id} />;
}
