const express = require('express');
const http = require('http');
const socketIo = require('socket.io');
const mongoose = require('mongoose');
const { Semaphore } = require('await-semaphore');

// Conectar ao MongoDB
mongoose.connect('mongodb://localhost:27017/eventos', { useNewUrlParser: true, useUnifiedTopology: true });

const app = express();
const server = http.createServer(app);
const io = socketIo(server);

// Model para o Evento
const eventSchema = new mongoose.Schema({
  nome: String,
  vagasDisponiveis: Number,
  reservas: [String]  // Guarda os nomes dos usuários que confirmaram
});

const Evento = mongoose.model('Evento', eventSchema);

// Configuração do Semáforo para limitar o número de usuários simultâneos
const semaforo = new Semaphore(3);  // No máximo 3 usuários podem interagir com os eventos ao mesmo tempo

// Fila de Espera
let filaEspera = [];

// Função para reservar vaga
const reservarVaga = async (socket, eventoId, usuarioNome) => {
  const evento = await Evento.findById(eventoId);

  // Checa se há vagas disponíveis
  if (evento.vagasDisponiveis > 0) {
    evento.vagasDisponiveis -= 1;
    evento.reservas.push(usuarioNome);
    await evento.save();
    socket.emit('reservaConfirmada', { eventoId, usuarioNome });
  } else {
    filaEspera.push(usuarioNome);
    socket.emit('filaEspera', filaEspera);
  }

  io.emit('atualizarEventos');  // Notifica todos os clientes sobre a atualização de eventos
};

// Timeout de 2 minutos para confirmação da reserva
const iniciarTimeout = (socket, eventoId, usuarioNome) => {
  setTimeout(async () => {
    const evento = await Evento.findById(eventoId);
    evento.vagasDisponiveis += 1; // Libera a vaga após o timeout

    // Se houver alguém na fila, ele pode reservar
    if (filaEspera.length > 0) {
      const proximoUsuario = filaEspera.shift();
      socket.emit('filaEspera', filaEspera);
      reservarVaga(socket, eventoId, proximoUsuario);  // Tenta reservar para o próximo usuário
    }

    await evento.save();
    io.emit('atualizarEventos');
  }, 2 * 60 * 1000);  // Timeout de 2 minutos
};

// Socket.io: Comunicação com os usuários
io.on('connection', (socket) => {
  console.log('Novo usuário conectado');

  // Enviar todos os eventos para o cliente quando ele se conectar
  Evento.find().then((eventos) => {
    socket.emit('atualizarEventos', eventos);
  });

  socket.on('reservarVaga', async (eventoId, usuarioNome) => {
    await semaforo.acquire();  // Adquire um "lock" para garantir que a reserva será feita de forma sequencial
    await reservarVaga(socket, eventoId, usuarioNome);
    iniciarTimeout(socket, eventoId, usuarioNome);
    semaforo.release();  // Libera o "lock"
  });

  socket.on('disconnect', () => {
    console.log('Usuário desconectado');
  });
});

// Rota para configurar eventos no backend
app.post('/admin/configurarEvento', async (req, res) => {
  const { nome, vagas } = req.body;
  const novoEvento = new Evento({ nome, vagasDisponiveis: vagas, reservas: [] });
  await novoEvento.save();
  res.send({ mensagem: 'Evento configurado com sucesso!' });
});

server.listen(3000, () => {
  console.log('Servidor rodando na porta 3000');
});
