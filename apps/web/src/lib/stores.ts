'use client';
import {create} from 'zustand';
import {createJSONStorage, persist} from 'zustand/middleware';

export type Tool = 'none' | 'highlight' | 'underline' | 'area' | 'note';
export type Dock = 'top' | 'bottom' | 'left' | 'right';
export type RightPanel = 'references' | 'figures' | 'tables' | 'notes';
type Theme = 'paper' | 'warm' | 'dark' | 'custom';
type Preferences = {
  fontFamily: string; fontSize: number; lineHeight: number; contentWidth: number; theme: Theme; toolbarDock: Dock; activeColor: string;
  customApp: string; customPaper: string; customText: string; customAccent: string;
  set: (values: Partial<Omit<Preferences, 'set'>>) => void;
};
export const usePreferences = create<Preferences>()(persist(set => ({
  fontFamily: 'Georgia, serif', fontSize: 18, lineHeight: 1.72, contentWidth: 800, theme: 'paper', toolbarDock: 'top', activeColor: '#f8d86a',
  customApp: '#e9f0eb', customPaper: '#ffffff', customText: '#26352f', customAccent: '#366f5e',
  set: values => set(values),
}), {name: 'paperlight-v2-preferences', storage: createJSONStorage(() => localStorage), skipHydration: true}));

type Layout = {focus: boolean; leftOpen: boolean; rightOpen: boolean; rightPanel: RightPanel; settingsOpen: boolean; historyOpen: boolean; set: (values: Partial<Omit<Layout, 'set'>>) => void};
export const useLayout = create<Layout>(set => ({
  focus: false, leftOpen: true, rightOpen: true, rightPanel: 'references', settingsOpen: false, historyOpen: false,
  set: values => set(values),
}));
type AnnotationUI = {activeTool: Tool; setTool: (tool: Tool) => void};
export const useAnnotationUI = create<AnnotationUI>(set => ({activeTool: 'highlight', setTool: activeTool => set({activeTool})}));
