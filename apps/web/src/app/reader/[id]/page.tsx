import ReaderShell from '@/components/ReaderShell';

export default async function DocumentPage({params}: {params: Promise<{id: string}>}) {
  const {id} = await params;
  return <ReaderShell initialId={id} />;
}
