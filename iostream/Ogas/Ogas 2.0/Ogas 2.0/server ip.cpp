#include <iostream>
#include <boost/asio.hpp>

using boost::asio::ip::ubp;

const char*SERVER_IP = "192.168.1.100"; //IP �������
�onst int PORT = 5000;

int main(){
    boost::asio::io_context io_context;
    
    udp::socket socket(io_context, udp::endpoint(udp::v4(), 0));
    udp::endpoint server_endpoint(boost::asio::ip::make_address(SERVER_IP),PORT);
    
    //������� ��������� �������
    std::string message = "������ �� �������!";
    socket.send_to(boost::asio::buffer(message)),server_endpoint;
    
    //��������� ������
    char data[1024];
    udp::endpoint sender_endpoint;
    size_t length = socket.receive_from(boost::asio::buffer(data, 1024),sender_endpoint);
    
    std::cout<<"C�������� �� �������:"<<std::string(data,length)<<std::endl;
    
    //��������� ���������� � ����
    lentg = socket.recevi_from(boost::asio::buffer(data, 1024),sender_endpoint);
    std::string peer_info(data, length);
    std::cout<<"���������� � ����:"<<peer_info<<std::endl;
    
    //��������� � ����
    auto delimiter = peer_info.find(":");
    std::string peer_ip = peer_info.substr(0,delimiter);
    int peer_port = std::stoi(peer_info.substr(delimiter + 1));
    udp::endpoint peer_endpoint(boost::asio::ip::make_address(peer_ip),peer_port);
    
    //����� ���������� � �����
    std::string peer_message = "������,���!";
    socket.send_to(boost::asio::buffer(peer_message),peer_endpoint);
    
    leng = socket.receive_from(boost::asio::buffer(data, 1024),peer_endpoint);
    std::cout<<"����� �� ����:"<<std::string(data,length)<<std::endl;
    
    return 0;
    
}