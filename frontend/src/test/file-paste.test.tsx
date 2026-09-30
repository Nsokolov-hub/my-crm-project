import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { api } from '../lib/api';
import { FilesPanel } from '../pages/Communication';

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api).mockImplementation(async (_path, options) =>
    options?.method === 'POST' ? { id: 'file-1' } : { items: [], total: 0 },
  );
});

it('uploads a screenshot pasted into the request', async () => {
  render(<FilesPanel entityType="request" entityId="request-1" />);
  const screenshot = new File(['png content'], 'clipboard.png', { type: 'image/png' });
  const event = new Event('paste', { bubbles: true, cancelable: true });
  Object.defineProperty(event, 'clipboardData', {
    value: { items: [{ type: 'image/png', getAsFile: () => screenshot }] },
  });
  fireEvent(document, event);

  await waitFor(() => expect(api).toHaveBeenCalledWith('/files', expect.objectContaining({ method: 'POST' })));
  const upload = vi.mocked(api).mock.calls.find(([path, options]) => path === '/files' && options?.method === 'POST');
  const body = upload?.[1]?.body as FormData;
  expect(body.get('entity_type')).toBe('request');
  expect(body.get('entity_id')).toBe('request-1');
  expect((body.get('file') as File).name).toMatch(/^Снимок-\d+\.png$/);
});

it('opens the file picker and uploads a selected PNG', async () => {
  const view = render(<FilesPanel entityType="request" entityId="request-1" />);
  const input = view.container.querySelector('input[type="file"]') as HTMLInputElement;
  const click = vi.spyOn(input, 'click');
  fireEvent.click(screen.getByRole('button', { name: 'Прикрепить файл' }));
  expect(click).toHaveBeenCalledOnce();

  const screenshot = new File(['png content'], 'screenshot.png', { type: 'image/png' });
  fireEvent.change(input, { target: { files: [screenshot] } });
  await waitFor(() => expect(api).toHaveBeenCalledWith('/files', expect.objectContaining({ method: 'POST' })));
  const upload = vi.mocked(api).mock.calls.find(([path, options]) => path === '/files' && options?.method === 'POST');
  expect(((upload?.[1]?.body as FormData).get('file') as File).name).toBe('screenshot.png');
});
