from sqlalchemy import inspect, text

from app import Destination, app, db

AIRPORT_CODES = {
    'Leh Ladakh': 'IXL',
    'Goa': 'GOI',
    'Kerala': 'COK',
    'Manali': 'KUU',
    'Jaipur': 'JAI',
    'Andaman': 'IXZ',
}


with app.app_context():
    tables = inspect(db.engine).get_table_names()
    if 'destinations' not in tables:
        print('No destinations table found; create the application database first.')
    else:
        columns = {column['name'] for column in inspect(db.engine).get_columns('destinations')}
        if 'airport_iata' not in columns:
            # Adding a nullable column keeps every existing destination row.
            db.session.execute(text(
                'ALTER TABLE destinations ADD COLUMN airport_iata VARCHAR(3)'
            ))
            db.session.commit()

        for destination_name, airport_code in AIRPORT_CODES.items():
            destination = Destination.query.filter(
                db.func.lower(Destination.name) == destination_name.lower()
            ).first()
            if destination:
                destination.airport_iata = airport_code
                print(f'{destination.name}: {airport_code}')

        db.session.commit()
