"""What the tool knows of each table: its endpoints, where its rows sit, sorting and counting."""
from dataclasses import dataclass, field


@dataclass(frozen=True, kw_only=True)
class Table:
    name: str  # the FROM name
    row_key: str  # each row of a reply sits under this key
    # Field name in a statement -> the server's sort name. Without a `query`, the server
    # drops an unknown sort name silently, so the tool checks them itself.
    sortable: dict
    columns: tuple  # FORMAT table without a column list
    # body: returnFormat count, the number is the reply; header: X-Result-Count of a
    # one-row metadata page
    count: str
    aliases: dict = field(default_factory=dict)  # column list: query field name -> row key
    # Rows the tool flattens are fetched with metadata: 1. It leaves out only lists of
    # records (an event's attributes, objects, reports), which flattening drops anyway.
    metadata: bool = False
    # MISP leaves rows out after reading them (warninglists, decaying, proposal blocking) and
    # counts them in X-Skipped-Elements-Count.
    filtered_after_read: bool = False
    path: str = '/{name}/{action}'  # the endpoints, action being restSearch or queryDescribe

    @property
    def search(self):
        return self.path.format(name=self.name, action='restSearch')

    @property
    def discovery(self):
        return self.path.format(name=self.name, action='queryDescribe')


TABLES = {table.name: table for table in (
    Table(
        name='attributes',
        row_key='Attribute',
        sortable={
            **{name: name for name in (
                'id', 'event_id', 'object_id', 'type', 'category', 'value', 'distribution',
                'timestamp', 'object_relation',
            )},
            'publish_timestamp': 'Event.publish_timestamp',
        },
        columns=('id', 'event_id', 'category', 'type', 'value', 'to_ids', 'timestamp', 'Tag'),
        count='body',
        filtered_after_read=True,
        aliases={
            'eventinfo': 'Event.info', 'org': 'Event.Orgc.name', 'tags': 'Tag',
            'event_timestamp': 'Event.timestamp', 'publish_timestamp': 'Event.publish_timestamp',
            'threat_level_id': 'Event.threat_level_id',
        },
    ),
    Table(
        name='events',
        row_key='Event',
        sortable={
            'id': 'id', 'event_id': 'id', 'eventinfo': 'info', 'analysis': 'analysis',
            'threat_level_id': 'threat_level_id', 'distribution': 'distribution',
            'timestamp': 'timestamp', 'event_timestamp': 'timestamp',
            'publish_timestamp': 'publish_timestamp',
        },
        columns=('id', 'date', 'info', 'Orgc.name', 'threat_level_id', 'analysis', 'published',
                 'attribute_count', 'Tag'),
        count='header',
        aliases={
            'event_id': 'id', 'eventinfo': 'info', 'org': 'Orgc.name', 'event_timestamp': 'timestamp',
            'tags': 'Tag',
        },
        metadata=True,
    ),
    Table(
        name='objects',
        row_key='Object',
        sortable={},
        columns=('id', 'event_id', 'name', 'meta-category', 'comment', 'timestamp'),
        count='body',
        aliases={
            'object_id': 'id', 'object_name': 'name', 'object_template_uuid': 'template_uuid',
            'orgc_id': 'Event.orgc_id',
        },
    ),
    Table(
        name='galaxy_clusters',
        row_key='GalaxyCluster',
        sortable={name: name for name in (
            'id', 'version', 'type', 'value', 'distribution', 'orgc_id', 'org_id', 'tag_name',
            'galaxy_id',
        )},
        columns=('id', 'value', 'Galaxy.name', 'Orgc.name', 'distribution', 'default'),
        count='body',
        aliases={
            'org': 'Orgc.name', 'galaxy_uuid': 'Galaxy.uuid', 'galaxy_name': 'Galaxy.name',
            'galaxy_namespace': 'Galaxy.namespace',
        },
    ),
    Table(
        name='event_reports',
        row_key='EventReport',
        sortable={name: name for name in ('id', 'event_id', 'name', 'timestamp', 'distribution')},
        columns=('id', 'event_id', 'name', 'Event.info', 'distribution', 'timestamp', 'Tag'),
        count='body',
        aliases={
            'eventinfo': 'Event.info', 'org': 'Event.Orgc.name', 'org_id': 'Event.org_id',
            'orgc_id': 'Event.orgc_id', 'date': 'Event.date', 'tags': 'Tag',
        },
    ),
    Table(
        name='notes',
        row_key='Note',
        sortable={name: name for name in ('id', 'created', 'modified', 'distribution')},
        columns=('id', 'object_type', 'object_uuid', 'note', 'authors', 'created'),
        count='body',
        aliases={'org': 'Orgc.name'},
        path='/analyst_data/{action}/Note',
    ),
    Table(
        name='opinions',
        row_key='Opinion',
        sortable={name: name for name in ('id', 'created', 'modified', 'distribution', 'opinion')},
        columns=('id', 'object_type', 'object_uuid', 'opinion', 'comment', 'authors'),
        count='body',
        aliases={'org': 'Orgc.name'},
        path='/analyst_data/{action}/Opinion',
    ),
    Table(
        name='relationships',
        row_key='Relationship',
        sortable={name: name for name in (
            'id', 'created', 'modified', 'distribution', 'relationship_type',
        )},
        columns=('id', 'object_type', 'object_uuid', 'relationship_type', 'related_object_type',
                 'related_object_uuid'),
        count='body',
        aliases={'org': 'Orgc.name'},
        path='/analyst_data/{action}/Relationship',
    ),
    Table(
        name='collections',
        row_key='Collection',
        sortable={name: name for name in (
            'id', 'name', 'type', 'created', 'modified', 'distribution',
        )},
        columns=('id', 'name', 'type', 'Orgc.name', 'distribution', 'element_count', 'modified'),
        count='body',
        aliases={'org': 'Orgc.name'},
    ),
    Table(
        name='tags',
        row_key='Tag',
        sortable={name: name for name in ('id', 'name', 'numerical_value')},
        columns=('id', 'name', 'numerical_value', 'hide_tag', 'is_galaxy', 'count', 'attribute_count'),
        count='body',
    ),
    Table(
        name='organisations',
        row_key='Organisation',
        sortable={name: name for name in ('id', 'name', 'date_created', 'date_modified')},
        columns=('id', 'name', 'type', 'nationality', 'sector', 'local', 'user_count'),
        count='body',
    ),
)}


def names():
    """The table names as a sentence: a, b or c."""
    *rest, last = TABLES
    return f'{", ".join(rest)} or {last}'
