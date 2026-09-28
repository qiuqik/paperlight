'use client';
import {create} from 'zustand';

export type MarkStyle = 'highlight' | 'underline' | 'area';
export type Dock = 'top' | 'bottom' | 'left' | 'right';
export type RightPanel = 'references' | 'figures' | 'tables' | 'notes';
type Theme = 'paper' | 'warm' | 'dark' | 'custom';
type Preferences = {
  fontFamily: string; fontSize: number; lineHeight: number; contentWidth: number; theme: Theme; toolbarDock: Dock; activeColor: string;
  customApp: string; customPaper: string; customText: string; customAccent: string;
  set: (values: Partial<Omit<Preferences, 'set'>>) => void;
  reset: () => void;
};
const defaults = {
  fontFamily: 'Georgia, serif', fontSize: 18, lineHeight: 1.72, contentWidth: 800, theme: 'paper', toolbarDock: 'top', activeColor: '#f8d86a',
  customApp: '#e9f0eb', customPaper: '#ffffff', customText: '#26352f', customAccent: '#366f5e',
} as const;
export const usePreferences = create<Preferences>(set => ({
  ...defaults,
  set: values => set(values),
  reset: () => set(defaults),
}));

type Layout = {focus: boolean; leftOpen: boolean; rightOpen: boolean; rightPanel: RightPanel; settingsOpen: boolean; historyOpen: boolean; set: (values: Partial<Omit<Layout, 'set'>>) => void};
export const useLayout = create<Layout>(set => ({
  focus: false, leftOpen: true, rightOpen: true, rightPanel: 'references', settingsOpen: false, historyOpen: false,
  set: values => set(values),
}));
type AnnotationUI = {markStyle: MarkStyle; noteEnabled: boolean; setStyle: (style: MarkStyle) => void; setNoteEnabled: (enabled: boolean) => void};
export const useAnnotationUI = create<AnnotationUI>(set => ({
  markStyle: 'highlight', noteEnabled: false,
  setStyle: markStyle => set({markStyle}), setNoteEnabled: noteEnabled => set({noteEnabled}),
}));
