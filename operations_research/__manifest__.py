{
    'name': 'Operations Research: Best Plans for Any Model (OR-Tools)',
    'version': '20.0.1.0.0',
    'category': 'Productivity',
    'summary': 'Find the best plan inside Odoo: quantities, assignments, schedules and delivery routes '
               'with Google OR-Tools. Review, then apply. Videos and guided tours included.',
    'description': """
Operations Research for Odoo
============================
Every planning question has three parts: what you DECIDE, your GOAL and your LIMITS.
Describe them once on any model (Sales, Purchase, Inventory, Accounting or your own),
press Solve, review the proposal and apply it to your records.

* Linear / integer programs, assignment, scheduling (Gantt) and vehicle routing (map)
* Review-then-apply with conflict detection, scenarios (what-if), sensitivity analysis
* Background queue, scheduled runs, multi-company, three access levels
* Dashboard, tutorial video and interactive guided tours inside Odoo
* English and Arabic (RTL)
""",
    'author': 'Abdelrahman Ashraf',
    'website': 'https://edu-abdelrahman-ashraf.odoo.com',
    'license': 'LGPL-3',
    'images': ['static/description/banner.png'],
    'depends': ['base', 'mail', 'web'],
    'external_dependencies': {'python': ['ortools']},
    'data': [
        'security/or_security.xml',
        'security/ir.access.csv',
        'data/ir_sequence_data.xml',
        'data/ir_cron_data.xml',
        'views/or_problem_views.xml',
        'views/or_run_views.xml',
        'views/res_config_settings_views.xml',
        'views/or_menus.xml',
        'views/or_scheduling_views.xml',
        'views/or_routing_views.xml',
        'views/or_dashboard_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'operations_research/static/src/core/or_core.scss',
            'operations_research/static/src/gantt/*',
            'operations_research/static/src/map/*',
            'operations_research/static/src/dashboard/*',
            'operations_research/static/src/guide/or_guide_service.js',
            'operations_research/static/src/guide/or_guide_overlay.*',
            'operations_research/static/src/guide/or_guide.scss',
            'operations_research/static/src/guide/or_guide_tours.js',
            'operations_research/static/src/tutorials/*',
        ],
        'web.assets_tests': [
            'operations_research/static/tests/tours/*.js',
        ],
    },
    'application': True,
    'installable': True,
}
