import { Star } from 'lucide-react';
import { Button, ErrorBox } from './ui';
import { useCommand } from '../lib/hooks';
import { queryClient } from '../lib/queryClient';
import type { Entity } from '../lib/types';
import '../styles/supplier-favorites.scss';

export function SupplierFavoriteButton({
  supplier,
  onChange,
}: {
  supplier: Entity;
  onChange?: (favorite: boolean) => void;
}) {
  const command = useCommand();
  const favorite = Boolean(supplier.is_favorite);
  return (
    <div className="supplier-favorite-control">
      <Button
        type="button"
        variant="ghost"
        busy={command.busy}
        aria-pressed={favorite}
        aria-label={`${favorite ? 'Убрать' : 'Добавить'} ${String(supplier.name)} ${favorite ? 'из избранного' : 'в избранное'}`}
        title="Ваш личный список избранных поставщиков"
        onClick={() => {
          void command
            .run<{ id: string; is_favorite: boolean }>(
              `/counterparties/${supplier.id}/favorite`,
              { favorite: !favorite },
              'PUT',
            )
            .then((result) => {
              if (!result) return;
              onChange?.(result.is_favorite);
              void queryClient.invalidateQueries({
                predicate: (query) =>
                  query.queryKey[0] === 'api' &&
                  typeof query.queryKey[1] === 'string' &&
                  query.queryKey[1].startsWith('/counterparties'),
              });
            })
            .catch(() => {
              /* The failed preference is shown here; the recipient and mail stay unchanged. */
            });
        }}
      >
        <Star size={15} fill={favorite ? 'currentColor' : 'none'} />
        {favorite ? 'Избранный' : 'В избранное'}
      </Button>
      <ErrorBox error={command.error} />
    </div>
  );
}
