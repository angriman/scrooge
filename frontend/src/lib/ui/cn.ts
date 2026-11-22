import { clsx, type ClassValue } from 'clsx';
import { extendTailwindMerge } from 'tailwind-merge';

// tailwind-merge knows Tailwind's own scales; ours are taught to it, so that `text-caption`
// passed in replaces a component's `text-body` instead of sitting beside it.
const merge = extendTailwindMerge({
	extend: {
		theme: {
			text: ['caption', 'small', 'body', 'title', 'heading', 'figure', 'hero'],
			radius: ['mark', 'control', 'panel', 'lead']
		}
	}
});

/** The classes of `inputs` as one string; of two that set the same thing, the last wins. */
export function cn(...inputs: ClassValue[]): string {
	return merge(clsx(inputs));
}
