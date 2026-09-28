import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { RecordForm } from '../components/Form';
import { api } from '../lib/api';
import { clientFields } from '../lib/fields';

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  const browserCrypto = globalThis.crypto;
  vi.stubGlobal('crypto', { getRandomValues: browserCrypto.getRandomValues.bind(browserCrypto) });
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute('open', '');
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute('open');
  };
  vi.mocked(api).mockImplementation(async (_path, options) =>
    options?.method === 'POST' ? { id: 'new-client' } : { items: [], total: 0 },
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('counterparty details', () => {
  it('saves ordinary text as structured requisites', async () => {
    render(
      <RecordForm
        title="Добавить контрагента"
        fields={clientFields}
        endpoint="/counterparties"
        onClose={vi.fn()}
        onSuccess={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText(/Название организации/), {
      target: { value: 'ООО Тест' },
    });
    fireEvent.change(screen.getByLabelText('Юридический адрес'), {
      target: { value: 'Москва, ул. Примерная, 1' },
    });
    fireEvent.change(screen.getByLabelText('Дополнительные сведения'), {
      target: { value: 'Отгрузка по будням' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));

    await waitFor(() =>
      expect(api).toHaveBeenCalledWith(
        '/counterparties',
        expect.objectContaining({
          method: 'POST',
          body: expect.objectContaining({
            name: 'ООО Тест',
            kind: 'client',
            details: {
              'Юридический адрес': 'Москва, ул. Примерная, 1',
              'Дополнительные сведения': 'Отгрузка по будням',
            },
          }),
        }),
      ),
    );
  });

  it('keeps previously imported details when editing bank information', async () => {
    render(
      <RecordForm
        title="Редактировать контрагента"
        fields={clientFields}
        endpoint="/counterparties/existing"
        method="PATCH"
        initial={{
          name: 'ООО Клиент',
          kind: 'client',
          details: { city: 'Казань', comment: 'Импортировано из XLSX' },
        }}
        extra={{ version: 2 }}
        onClose={vi.fn()}
        onSuccess={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText('Банк'), {
      target: { value: 'Тестовый банк' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));

    await waitFor(() =>
      expect(api).toHaveBeenCalledWith(
        '/counterparties/existing',
        expect.objectContaining({
          method: 'PATCH',
          body: expect.objectContaining({
            version: 2,
            details: {
              city: 'Казань',
              comment: 'Импортировано из XLSX',
              'Банк': 'Тестовый банк',
            },
          }),
        }),
      ),
    );
  });
});
