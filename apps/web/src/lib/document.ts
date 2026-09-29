export type Box = {x: number; y: number; width: number; height: number};
export type InlineNode = {type: string; text?: string; display?: string; latex?: string; bold?: boolean; italic?: boolean; referenceIds?: string[]; href?: string; figureId?: string; tableId?: string; src?: string; number?: number};
export type Block = {
  id: string; type: string; text?: string; latex?: string; content?: InlineNode[]; page?: number; bbox?: Box; order?: number;
  items?: string[]; number?: number; label?: string; caption?: string; src?: string;
  captionContent?: InlineNode[];
  beforeHeading?: boolean;
  headers?: Array<{text: string} | string>; rows?: Array<Array<{text: string} | string>>;
};
export type Section = {id: string; title: string; level: number; type?: string; presentation?: 'article' | 'prompt'; blocks: Block[]};
export type Reference = {id: string; number: number; authors: string; title: string; venue?: string; year?: number; doi?: string; preview?: string};
export type PageGeometry = {number: number; width: number; height: number};
export type DocumentModel = {
  id: string; modelVersion?: number; fingerprint?: string; metadata: {title: string; authors: string[]; pageCount: number; readMinutes?: number; venue?: string; year?: number};
  sections: Section[]; references: Reference[]; figures: Block[]; tables: Block[]; pages?: PageGeometry[];
};
export const DOCUMENT_MODEL_VERSION = 3;
export type TextAnchor = {start: {blockId: string; offset: number}; end: {blockId: string; offset: number}; quote: string; prefix: string; suffix: string};
export type AreaAnchor = {blockId: string; page: number; bbox: Box; space?: 'page' | 'block'; surface?: 'image'};
export type Annotation = {id: string; documentId: string; type: 'highlight' | 'underline' | 'note' | 'area'; style?: 'highlight' | 'underline'; noteEnabled?: boolean; color: string; anchor: TextAnchor | AreaAnchor; note?: string; createdAt: number; updatedAt?: number};
export function isTextAnchor(anchor: Annotation['anchor']): anchor is TextAnchor {return 'start' in anchor;}
export function allBlocks(document: DocumentModel): Block[] {return document.sections.flatMap(section => section.blocks);}
export function resolveAssetSources(document: DocumentModel, serverId?: string): DocumentModel {
  const src = (value?: string) => {
    if (!value) return value;
    if (value.startsWith('/src/assets/')) return `/${value.split('/').pop()}`;
    if (serverId && value.startsWith('/api/documents/')) return `/api/parser${value}`;
    return value;
  };
  const fix = (block: Block) => ({...block, src: src(block.src), content: block.content?.map(node => ({...node, src: src(node.src)})), captionContent: block.captionContent?.map(node => ({...node, src: src(node.src)}))});
  return {...document, sections: document.sections.map(section => ({...section, blocks: section.blocks.map(fix)})), figures: document.figures.map(fix), tables: document.tables.map(fix)};
}
