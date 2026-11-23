import { describe, expect, it } from 'vitest';
import { cn } from './cn.js';

describe('cn', () => {
	it('lets a size passed in replace the component’s, and keeps its colour', () => {
		expect(cn('text-body text-ink', 'text-caption')).toBe('text-ink text-caption');
	});

	it('lets a corner passed in replace the component’s', () => {
		expect(cn('rounded-panel bg-panel', 'rounded-lead')).toBe('bg-panel rounded-lead');
	});

	it('leaves out what is false', () => {
		expect(cn('bg-panel', false && 'bg-raised', undefined)).toBe('bg-panel');
	});
});
