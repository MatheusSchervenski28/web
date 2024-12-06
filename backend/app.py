from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
from flask_cors import CORS
import time
import threading

app = Flask(__name__)
CORS(app)  # Permite todas as origens

# Configuração do SocketIO, com CORS e opção de forçar o uso de WebSocket
socketio = SocketIO(app, cors_allowed_origins="*", logger=True, engineio_logger=True)

# Lista de eventos e fila de espera
events = [
    {"id": 1, "name": "Evento 1", "available_spots": 5, "confirmed_by": [], "reserved_users": []},
    {"id": 2, "name": "Evento 2", "available_spots": 5, "confirmed_by": [], "reserved_users": []},
    {"id": 3, "name": "Evento 3", "available_spots": 5, "confirmed_by": [], "reserved_users": []},
    {"id": 4, "name": "Evento 4", "available_spots": 5, "confirmed_by": [], "reserved_users": []},
    {"id": 5, "name": "Evento 5", "available_spots": 5, "confirmed_by": [], "reserved_users": []},
]

waiting_queue = []

# Variável para contar usuários online
online_users = 0

# Número máximo de usuários online
MAX_USERS_ONLINE = 3

# Semáforo para controle de concorrência
max_concurrent_reservations = 3
current_reservations = 0
reservation_lock = threading.Lock()

# Tempo de expiração para confirmação (em segundos)
reservation_timeout = 120  # 2 minutos

# Armazena os tempos restantes para as confirmações
reserved_times = {}

def check_timeouts():
    """Verifica o tempo de expiração das reservas e libera vagas se necessário."""
    while True:
        time.sleep(1)  # Checa a cada 1 segundo
        for event in events:
            if event["reserved_users"]:
                for user in event["reserved_users"]:
                    event_id = event['id']
                    elapsed_time = time.time() - user['timestamp']
                    remaining_time = reservation_timeout - elapsed_time
                    
                    if remaining_time > 0:
                        # Emite o tempo restante para os clientes
                        socketio.emit('time_left', {'eventId': event_id, 'timeLeft': int(remaining_time)})
                    else:
                        # Se o tempo de expiração passou, cancela a reserva
                        event["reserved_users"].remove(user)
                        event["available_spots"] += 1
                        socketio.emit('event_updated', {'event': event})
                        socketio.emit('queue_updated', {'queue': waiting_queue})

                        # Chama o próximo usuário na fila, se houver
                        if waiting_queue:
                            next_user = waiting_queue.pop(0)
                            reserve_user_in_event(event, next_user)

        socketio.emit('events_updated', {'events': events})

def reserve_user_in_event(event, user_name):
    """Reserva o próximo usuário da fila para o evento."""
    event['reserved_users'].append({'name': user_name, 'timestamp': time.time()})
    event['available_spots'] -= 1
    socketio.emit('event_reserved', {'event': event, 'user_name': user_name})
    socketio.emit('queue_updated', {'queue': waiting_queue})
    socketio.emit('user_joined', {'user_name': user_name, 'event': event})

# Função para criar um novo evento
@app.route('/create_event', methods=['POST'])
def create_event():
    """Cria um novo evento."""
    data = request.get_json()
    event_name = data['event_name']
    available_spots = data['available_spots']

    # Verifica se o evento já existe
    if any(event['name'] == event_name for event in events):
        return jsonify({"message": "Evento já existe!"}), 400

    event_id = len(events) + 1
    new_event = {"id": event_id, "name": event_name, "available_spots": available_spots, "confirmed_by": [], "reserved_users": []}
    events.append(new_event)

    # Emite o evento para todos os clientes
    socketio.emit('event_created', {'event': new_event})
    socketio.emit('events_updated', {'events': events})

    return jsonify({"message": "Evento criado com sucesso!", "event": new_event}), 200

@app.route('/reserve', methods=['POST'])
def reserve():
    event_id = request.json['event_id']
    user_name = request.json['user_name']
    
    # Verifica se o nome do usuário já foi reservado
    for event in events:
        if any(user['name'] == user_name for user in event['reserved_users']):
            return jsonify({"message": "Você já tem uma reserva em um evento!"}), 400
    
    with reservation_lock:
        event = next((e for e in events if e['id'] == event_id), None)
        if event and event['available_spots'] > 0:
            # Adiciona o usuário à lista de reservas temporárias
            event['reserved_users'].append({'name': user_name, 'timestamp': time.time()})
            event['available_spots'] -= 1
            socketio.emit('event_reserved', {'event': event, 'user_name': user_name})
            return jsonify({"message": "Reserva temporária realizada. Confirme dentro de 2 minutos."}), 200
        else:
            waiting_queue.append(user_name)
            socketio.emit('queue_updated', {'queue': waiting_queue})
            return jsonify({"message": "Sem vagas disponíveis, você foi adicionado à fila de espera."}), 200

@app.route('/confirm', methods=['POST'])
def confirm():
    event_id = request.json['event_id']
    user_name = request.json['user_name']
    
    event = next((e for e in events if e['id'] == event_id), None)
    if event:
        # Confirma a reserva
        if not any(user['name'] == user_name for user in event['reserved_users']):
            return jsonify({"message": "Reserva não encontrada!"}), 400
        event['confirmed_by'].append(user_name)
        socketio.emit('reservation_confirmed', {'event': event, 'user_name': user_name})
        return jsonify({"message": "Reserva confirmada!"}), 200
    return jsonify({"message": "Erro ao confirmar a reserva."}), 400

@app.route('/cancel', methods=['POST'])
def cancel_reservation():
    event_id = request.json['event_id']
    user_name = request.json['user_name']
    
    event = next((e for e in events if e['id'] == event_id), None)
    if event:
        # Cancela a reserva
        event['reserved_users'] = [user for user in event['reserved_users'] if user['name'] != user_name]
        event['available_spots'] += 1
        socketio.emit('reservation_cancelled', {'event': event, 'user_name': user_name})
        return jsonify({"message": "Reserva cancelada."}), 200
    return jsonify({"message": "Erro ao cancelar a reserva."}), 400

# Eventos de conexão e desconexão de usuários
@socketio.on('connect')
def handle_connect():
    global online_users
    if online_users < MAX_USERS_ONLINE:
        online_users += 1
        socketio.emit('user_count', {'online_users': online_users})
    else:
        # Recusa a conexão se o limite for atingido
        return False  # Impede a conexão de mais usuários

@socketio.on('disconnect')
def handle_disconnect():
    global online_users
    online_users -= 1
    socketio.emit('user_count', {'online_users': online_users})

# Inicia a thread para verificar timeouts
thread = threading.Thread(target=check_timeouts)
thread.daemon = True
thread.start()

# Rota principal
@app.route('/')
def index():
    return "Servidor Flask em execução!"

if __name__ == '__main__':
    socketio.run(app, debug=True)
