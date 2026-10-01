import { Pencil } from 'lucide-react';
import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAuth } from '../app/Auth';
import { Collection } from '../components/Collection';
import { RecordForm } from '../components/Form';
import { Button, DetailPairs, ErrorBox, Loading, Modal, PageHeading } from '../components/ui';
import { contactFields } from '../lib/fields';
import { useApi } from '../lib/hooks';

export function ContactCard({
  contactId,
  onClose,
  onSaved,
}: {
  contactId: string;
  onClose: () => void;
  onSaved?: () => void;
}) {
  const auth = useAuth();
  const contact = useApi<import('../lib/types').Entity>(`/contacts/${contactId}`);
  const [editing, setEditing] = useState(false);
  if (editing && contact.data)
    return (
      <RecordForm
        title="Редактировать контакт"
        endpoint={`/contacts/${contactId}`}
        method="PATCH"
        fields={contactFields}
        initial={contact.data}
        extra={{ version: contact.data.version }}
        onClose={() => setEditing(false)}
        onSuccess={() => {
          setEditing(false);
          contact.refresh();
          onSaved?.();
        }}
      />
    );
  return (
    <Modal title={String(contact.data?.name || 'Контакт')} onClose={onClose}>
      <div className="form-body">
        {contact.loading ? (
          <Loading />
        ) : (
          <>
            <ErrorBox error={contact.error} retry={contact.refresh} />
            {contact.data && (
              <DetailPairs
                values={{
                  Клиент: contact.data.client_name,
                  Имя: contact.data.name,
                  Должность: contact.data.position,
                  Отдел: contact.data.department,
                  'Направление закупок': contact.data.purchase_area,
                  Телефон: contact.data.phone,
                  Почта: contact.data.email,
                  Комментарий: contact.data.comment,
                }}
              />
            )}
            {contact.data && auth.can('clients.write') && (
              <Button variant="secondary" onClick={() => setEditing(true)}>
                <Pencil size={15} /> Редактировать контакт
              </Button>
            )}
          </>
        )}
      </div>
    </Modal>
  );
}

export function Contacts() {
  const auth = useAuth();
  const [params, setParams] = useSearchParams();
  const [revision, setRevision] = useState(0);
  const contactId = params.get('contact_id');
  return (
    <>
      <PageHeading title="Контакты" description="Закупщики и отделы клиентов рабочей базы." />
      <Collection
        title="Контактные лица"
        endpoint="/contacts"
        refreshKey={revision}
        canCreate={auth.can('clients.write')}
        createLabel="Добавить контакт"
        fields={[
          {
            name: 'client_id',
            label: 'Клиент рабочей базы',
            type: 'select',
            source: '/counterparties?kind=client&client_base=working',
            required: true,
          },
          ...contactFields,
        ]}
        onSelect={(row) => setParams({ contact_id: row.id })}
        columns={[
          { key: 'name', label: 'Имя' },
          { key: 'client_name', label: 'Клиент' },
          { key: 'department', label: 'Отдел' },
          { key: 'purchase_area', label: 'Направление закупок' },
          { key: 'phone', label: 'Телефон' },
          { key: 'email', label: 'Почта' },
        ]}
      />
      {contactId && (
        <ContactCard
          contactId={contactId}
          onClose={() => setParams({})}
          onSaved={() => setRevision((v) => v + 1)}
        />
      )}
    </>
  );
}
